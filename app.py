"""Content Crew: multi-brand AI content pipeline with human approval."""
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import agents
import db
import llm
from publisher import PROVIDER_LABELS, PublishError, publish_post

def _load_dotenv(path=".env"):
    """Tiny .env reader so local runs need no extra package."""
    env_file = Path(__file__).with_name(path)
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if value.strip():
                os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

PLATFORMS = ["TikTok", "Instagram", "YouTube", "Facebook"]
STAGES = ["ideas", "script", "design", "pack", "done"]


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    try:
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # read-only disk on serverless hosts; Postgres and SECRET_KEY are used there
    app.config.update(
        DATABASE=os.path.join(app.instance_path, "crew.sqlite"),
        DATABASE_URL=os.environ.get("DATABASE_URL", ""),
        MAX_POSTS_PER_CHANNEL_PER_DAY=int(os.environ.get("MAX_POSTS_PER_CHANNEL_PER_DAY", "3")),
        SECRET_KEY=os.environ.get("SECRET_KEY") or _persistent_secret(app.instance_path),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)

    app.teardown_appcontext(db.close_db)
    with app.app_context():
        db.init_db()

    # ---- helpers ----------------------------------------------------------
    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("uid")
        if uid:
            g.user = db.get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            token = request.form.get("csrf_token", "")
            if not token or token != session.get("csrf"):
                abort(400, "Invalid form token. Reload the page and try again.")

    @app.context_processor
    def inject():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"csrf_token": session["csrf"], "PLATFORMS": PLATFORMS, "PROVIDER_LABELS": PROVIDER_LABELS}

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapped

    def own_brand(brand_id):
        row = db.get_db().execute(
            "SELECT * FROM brands WHERE id=? AND user_id=?", (brand_id, g.user["id"])
        ).fetchone()
        if row is None:
            abort(404)
        return row

    def own_run(run_id):
        row = db.get_db().execute(
            "SELECT r.* FROM runs r JOIN brands b ON b.id=r.brand_id WHERE r.id=? AND b.user_id=?",
            (run_id, g.user["id"]),
        ).fetchone()
        if row is None:
            abort(404)
        return row

    def own_post(post_id):
        row = db.get_db().execute(
            "SELECT p.*, r.brand_id FROM posts p JOIN runs r ON r.id=p.run_id "
            "JOIN brands b ON b.id=r.brand_id WHERE p.id=? AND b.user_id=?",
            (post_id, g.user["id"]),
        ).fetchone()
        if row is None:
            abort(404)
        return row

    def record_usage(usage):
        month = utcnow().strftime("%Y-%m")
        conn = db.get_db()
        conn.execute(
            "INSERT INTO usage(user_id, month, calls, input_tokens, output_tokens) VALUES (?,?,1,?,?) "
            "ON CONFLICT(user_id, month) DO UPDATE SET calls=usage.calls+1, "
            "input_tokens=usage.input_tokens+excluded.input_tokens, output_tokens=usage.output_tokens+excluded.output_tokens",
            (g.user["id"], month, usage.get("input", 0), usage.get("output", 0)),
        )
        conn.commit()

    def call_agent(fn, *args):
        """Run an agent. Returns data, or None after flashing the error."""
        try:
            data, usage = fn(*args)
        except llm.LLMError as exc:
            flash(str(exc), "error")
            return None
        record_usage(usage)
        return data

    def brand_platforms(brand_id):
        rows = db.get_db().execute(
            "SELECT DISTINCT platform FROM channels WHERE brand_id=?", (brand_id,)
        ).fetchall()
        order = {p: i for i, p in enumerate(PLATFORMS)}
        return sorted((r["platform"] for r in rows), key=lambda p: order.get(p, 99))

    def loads(value):
        return json.loads(value) if value else None

    # ---- auth -------------------------------------------------------------
    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            if "@" not in email or len(password) < 8:
                flash("Enter a valid email and a password of at least 8 characters.", "error")
            else:
                conn = db.get_db()
                try:
                    user_id = conn.insert(
                        "INSERT INTO users(email, pw_hash, created_at) VALUES (?,?,?)",
                        (email, generate_password_hash(password), iso(utcnow())),
                    )
                    conn.commit()
                except db.IntegrityErrors:
                    conn.rollback()
                    flash("That email is already registered.", "error")
                else:
                    session.clear()
                    session["uid"] = user_id
                    return redirect(url_for("dashboard"))
        return render_template("auth.html", mode="register")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            user = db.get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if user and check_password_hash(user["pw_hash"], request.form.get("password", "")):
                session.clear()
                session["uid"] = user["id"]
                return redirect(url_for("dashboard"))
            flash("Wrong email or password.", "error")
        return render_template("auth.html", mode="login")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---- dashboard and brands ---------------------------------------------
    @app.route("/")
    @login_required
    def dashboard():
        conn = db.get_db()
        brands = conn.execute(
            "SELECT b.*, "
            "(SELECT COUNT(*) FROM channels c WHERE c.brand_id=b.id) AS channel_count, "
            "(SELECT COUNT(*) FROM posts p JOIN runs r ON r.id=p.run_id WHERE r.brand_id=b.id AND p.status='scheduled') AS queued "
            "FROM brands b WHERE b.user_id=? ORDER BY b.id DESC",
            (g.user["id"],),
        ).fetchall()
        usage = conn.execute(
            "SELECT * FROM usage WHERE user_id=? AND month=?", (g.user["id"], utcnow().strftime("%Y-%m"))
        ).fetchone()
        return render_template("dashboard.html", brands=brands, usage=usage, live=bool(os.environ.get("ANTHROPIC_API_KEY")))

    @app.route("/brands", methods=["POST"])
    @login_required
    def create_brand():
        f = request.form
        name, niche = f.get("name", "").strip(), f.get("niche", "").strip()
        if not name or not niche:
            flash("A brand needs a name and a niche.", "error")
            return redirect(url_for("dashboard"))
        conn = db.get_db()
        new_id = conn.insert(
            "INSERT INTO brands(user_id,name,niche,audience,tone,rules,language,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                g.user["id"], name[:80], niche[:200], f.get("audience", "").strip()[:300],
                f.get("tone", "").strip()[:200], f.get("rules", "").strip()[:600],
                (f.get("language", "").strip() or "English")[:40], iso(utcnow()),
            ),
        )
        conn.commit()
        return redirect(url_for("brand_page", brand_id=new_id))

    @app.route("/brands/<int:brand_id>")
    @login_required
    def brand_page(brand_id):
        brand = own_brand(brand_id)
        conn = db.get_db()
        channels = conn.execute("SELECT * FROM channels WHERE brand_id=? ORDER BY id", (brand_id,)).fetchall()
        runs = conn.execute("SELECT * FROM runs WHERE brand_id=? ORDER BY id DESC LIMIT 20", (brand_id,)).fetchall()
        run_topics = {r["id"]: (loads(r["topic_json"]) or {}).get("title", "New run") for r in runs}
        posts = conn.execute(
            "SELECT p.*, c.platform, c.handle, r.topic_json FROM posts p "
            "JOIN channels c ON c.id=p.channel_id JOIN runs r ON r.id=p.run_id "
            "WHERE r.brand_id=? ORDER BY p.scheduled_at DESC LIMIT 60",
            (brand_id,),
        ).fetchall()
        totals = conn.execute(
            "SELECT c.platform, c.handle, COUNT(p.id) AS posts, "
            "COALESCE(SUM(CASE WHEN p.status='published' THEN 1 ELSE 0 END),0) AS published, "
            "COALESCE(SUM(p.views),0) AS views, COALESCE(SUM(p.likes),0) AS likes, "
            "COALESCE(SUM(p.followers_gained),0) AS followers, COALESCE(SUM(p.leads),0) AS leads "
            "FROM channels c LEFT JOIN posts p ON p.channel_id=c.id WHERE c.brand_id=? GROUP BY c.id",
            (brand_id,),
        ).fetchall()
        post_titles = {p["id"]: (loads(p["topic_json"]) or {}).get("title", "") for p in posts}
        return render_template(
            "brand.html", brand=brand, channels=channels, runs=runs, run_topics=run_topics,
            posts=posts, post_titles=post_titles, totals=totals, now=iso(utcnow()),
        )

    @app.route("/brands/<int:brand_id>/delete", methods=["POST"])
    @login_required
    def delete_brand(brand_id):
        own_brand(brand_id)
        conn = db.get_db()
        conn.execute("DELETE FROM brands WHERE id=?", (brand_id,))
        conn.commit()
        flash("Brand deleted.", "ok")
        return redirect(url_for("dashboard"))

    @app.route("/brands/<int:brand_id>/channels", methods=["POST"])
    @login_required
    def add_channel(brand_id):
        own_brand(brand_id)
        platform = request.form.get("platform", "")
        handle = request.form.get("handle", "").strip().lstrip("@")[:60]
        if platform not in PLATFORMS or not handle:
            flash("Pick a platform and enter the account handle.", "error")
        else:
            conn = db.get_db()
            conn.execute(
                "INSERT INTO channels(brand_id, platform, handle, provider, created_at) VALUES (?,?,?,?,?)",
                (brand_id, platform, handle, "dryrun", iso(utcnow())),
            )
            conn.commit()
        return redirect(url_for("brand_page", brand_id=brand_id))

    @app.route("/brands/<int:brand_id>/channels/<int:channel_id>/delete", methods=["POST"])
    @login_required
    def delete_channel(brand_id, channel_id):
        own_brand(brand_id)
        conn = db.get_db()
        conn.execute("DELETE FROM channels WHERE id=? AND brand_id=?", (channel_id, brand_id))
        conn.commit()
        return redirect(url_for("brand_page", brand_id=brand_id))

    # ---- runs (the agent pipeline) ----------------------------------------
    @app.route("/brands/<int:brand_id>/runs", methods=["POST"])
    @login_required
    def new_run(brand_id):
        brand = own_brand(brand_id)
        if not brand_platforms(brand_id):
            flash("Add at least one channel before starting a run.", "error")
            return redirect(url_for("brand_page", brand_id=brand_id))
        ideas = call_agent(agents.scout, brand)
        if ideas is None:
            return redirect(url_for("brand_page", brand_id=brand_id))
        conn = db.get_db()
        new_id = conn.insert(
            "INSERT INTO runs(brand_id, stage, ideas_json, created_at) VALUES (?,?,?,?)",
            (brand_id, "ideas", json.dumps(ideas), iso(utcnow())),
        )
        conn.commit()
        return redirect(url_for("run_page", run_id=new_id))

    @app.route("/runs/<int:run_id>")
    @login_required
    def run_page(run_id):
        run = own_run(run_id)
        brand = own_brand(run["brand_id"])
        view = {k: loads(run[k + "_json"]) for k in ("ideas", "topic", "script", "design", "pack")}
        platforms = brand_platforms(brand["id"])
        default_time = iso(utcnow() + timedelta(hours=1))
        return render_template(
            "run.html", run=run, brand=brand, v=view, platforms=platforms,
            stages=STAGES, default_time=default_time, cap=app.config["MAX_POSTS_PER_CHANNEL_PER_DAY"],
        )

    def save_run(run_id, **fields):
        conn = db.get_db()
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE runs SET {sets} WHERE id=?", (*fields.values(), run_id))
        conn.commit()

    @app.route("/runs/<int:run_id>/ideas", methods=["POST"])
    @login_required
    def new_ideas(run_id):
        run = own_run(run_id)
        if run["stage"] != "ideas":
            abort(400)
        ideas = call_agent(agents.scout, own_brand(run["brand_id"]))
        if ideas is not None:
            save_run(run_id, ideas_json=json.dumps(ideas))
        return redirect(url_for("run_page", run_id=run_id))

    @app.route("/runs/<int:run_id>/pick", methods=["POST"])
    @login_required
    def pick_topic(run_id):
        run = own_run(run_id)
        if run["stage"] != "ideas":
            abort(400)
        ideas = loads(run["ideas_json"]) or []
        try:
            topic = ideas[int(request.form.get("idx", "-1"))]
        except (ValueError, IndexError):
            abort(400)
        script = call_agent(agents.writer, own_brand(run["brand_id"]), topic, "")
        if script is not None:
            save_run(run_id, topic_json=json.dumps(topic), script_json=json.dumps(script), stage="script")
        return redirect(url_for("run_page", run_id=run_id))

    @app.route("/runs/<int:run_id>/redo", methods=["POST"])
    @login_required
    def redo(run_id):
        run = own_run(run_id)
        brand = own_brand(run["brand_id"])
        note = request.form.get("note", "").strip()[:400]
        topic, script = loads(run["topic_json"]), loads(run["script_json"])
        if run["stage"] == "script":
            data = call_agent(agents.writer, brand, topic, note)
            if data is not None:
                save_run(run_id, script_json=json.dumps(data))
        elif run["stage"] == "design":
            data = call_agent(agents.designer, brand, script, note)
            if data is not None:
                save_run(run_id, design_json=json.dumps(data))
        elif run["stage"] == "pack":
            data = call_agent(agents.publisher, brand, brand_platforms(brand["id"]), topic, script, note)
            if data is not None:
                save_run(run_id, pack_json=json.dumps(data))
        else:
            abort(400)
        return redirect(url_for("run_page", run_id=run_id))

    @app.route("/runs/<int:run_id>/approve", methods=["POST"])
    @login_required
    def approve(run_id):
        run = own_run(run_id)
        brand = own_brand(run["brand_id"])
        topic, script = loads(run["topic_json"]), loads(run["script_json"])
        if run["stage"] == "script":
            data = call_agent(agents.designer, brand, script, "")
            if data is not None:
                save_run(run_id, design_json=json.dumps(data), stage="design")
        elif run["stage"] == "design":
            data = call_agent(agents.publisher, brand, brand_platforms(brand["id"]), topic, script, "")
            if data is not None:
                save_run(run_id, pack_json=json.dumps(data), stage="pack")
        elif run["stage"] == "pack":
            return schedule_pack(run, brand)
        else:
            abort(400)
        return redirect(url_for("run_page", run_id=run_id))

    def schedule_pack(run, brand):
        pack = loads(run["pack_json"]) or []
        try:
            start = datetime.strptime(request.form.get("publish_at", ""), "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("Pick a valid publish time.", "error")
            return redirect(url_for("run_page", run_id=run["id"]))
        conn = db.get_db()
        channels = conn.execute("SELECT * FROM channels WHERE brand_id=? ORDER BY id", (brand["id"],)).fetchall()
        cap = app.config["MAX_POSTS_PER_CHANNEL_PER_DAY"]
        planned = []
        for i, channel in enumerate(channels):
            variant = next((v for v in pack if v["platform"] == channel["platform"]), None)
            if variant is None:
                continue
            when = start + timedelta(minutes=30 * i)
            day = when.strftime("%Y-%m-%d")
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM posts WHERE channel_id=? AND substr(scheduled_at,1,10)=? AND status!='failed'",
                (channel["id"], day),
            ).fetchone()["n"]
            if count >= cap:
                flash(f"@{channel['handle']} on {channel['platform']} already has {cap} posts on {day}. Pick another day.", "error")
                return redirect(url_for("run_page", run_id=run["id"]))
            key = variant["platform"]
            caption = request.form.get("cap_" + key, variant["caption"]).strip()[:2200] or variant["caption"]
            tags = request.form.get("tags_" + key, " ".join(variant.get("hashtags", []))).strip()[:300]
            planned.append((run["id"], channel["id"], caption, tags, iso(when)))
        if not planned:
            flash("No channel matches this pack. Add a channel first.", "error")
            return redirect(url_for("run_page", run_id=run["id"]))
        conn.executemany(
            "INSERT INTO posts(run_id, channel_id, caption, hashtags, scheduled_at) VALUES (?,?,?,?,?)", planned
        )
        conn.execute("UPDATE runs SET stage='done' WHERE id=?", (run["id"],))
        conn.commit()
        flash(f"{len(planned)} post(s) scheduled. Times are UTC.", "ok")
        return redirect(url_for("brand_page", brand_id=brand["id"]))

    # ---- publishing and results -------------------------------------------
    def publish_due_posts(brand_id=None):
        conn = db.get_db()
        sql = (
            "SELECT p.*, c.platform, c.handle, c.provider FROM posts p "
            "JOIN channels c ON c.id=p.channel_id JOIN runs r ON r.id=p.run_id "
            "JOIN brands b ON b.id=r.brand_id "
            "WHERE p.status='scheduled' AND p.scheduled_at<=? AND b.user_id=?"
        )
        args = [iso(utcnow()), g.user["id"]]
        if brand_id:
            sql += " AND b.id=?"
            args.append(brand_id)
        done = failed = 0
        for post in conn.execute(sql, args).fetchall():
            try:
                result = publish_post(post, post)
                conn.execute(
                    "UPDATE posts SET status='published', published_at=?, external_id=?, error=NULL WHERE id=?",
                    (iso(utcnow()), result["external_id"], post["id"]),
                )
                done += 1
            except PublishError as exc:
                conn.execute("UPDATE posts SET status='failed', error=? WHERE id=?", (str(exc)[:300], post["id"]))
                failed += 1
        conn.commit()
        return done, failed

    @app.route("/brands/<int:brand_id>/publish-due", methods=["POST"])
    @login_required
    def publish_due(brand_id):
        own_brand(brand_id)
        done, failed = publish_due_posts(brand_id)
        flash(f"Published {done} post(s) (simulated). Failed: {failed}.", "ok" if not failed else "error")
        return redirect(url_for("brand_page", brand_id=brand_id))

    @app.route("/posts/<int:post_id>/metrics", methods=["POST"])
    @login_required
    def post_metrics(post_id):
        post = own_post(post_id)
        if post["status"] != "published":
            abort(400)

        def num(name):
            try:
                return max(0, min(int(request.form.get(name, "0") or 0), 10**9))
            except ValueError:
                return 0

        conn = db.get_db()
        conn.execute(
            "UPDATE posts SET views=?, likes=?, followers_gained=?, leads=? WHERE id=?",
            (num("views"), num("likes"), num("followers"), num("leads"), post_id),
        )
        conn.commit()
        return redirect(url_for("brand_page", brand_id=post["brand_id"]) + "#results")

    @app.route("/posts/<int:post_id>/cancel", methods=["POST"])
    @login_required
    def cancel_post(post_id):
        post = own_post(post_id)
        if post["status"] == "scheduled":
            conn = db.get_db()
            conn.execute("DELETE FROM posts WHERE id=?", (post_id,))
            conn.commit()
        return redirect(url_for("brand_page", brand_id=post["brand_id"]))

    return app


def _persistent_secret(instance_path):
    path = Path(instance_path) / "secret_key"
    try:
        if path.exists():
            return path.read_text().strip()
        key = secrets.token_hex(32)
        path.write_text(key)
        return key
    except OSError as exc:
        raise RuntimeError("Set the SECRET_KEY environment variable (the disk is read-only).") from exc


if __name__ == "__main__":
    create_app().run(debug=bool(os.environ.get("FLASK_DEBUG")), host="127.0.0.1", port=int(os.environ.get("PORT", "5000")))
