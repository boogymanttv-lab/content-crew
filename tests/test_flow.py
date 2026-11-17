import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.pop("ANTHROPIC_API_KEY", None)

from app import create_app  # noqa: E402


PG_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture(params=["sqlite"] + (["postgres"] if PG_URL else []))
def client(request, tmp_path):
    config = {"TESTING": True, "DATABASE": str(tmp_path / "t.sqlite"), "SECRET_KEY": "test", "DATABASE_URL": ""}
    if request.param == "postgres":
        import psycopg

        with psycopg.connect(PG_URL, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        config["DATABASE_URL"] = PG_URL
    return create_app(config).test_client()


def token(client):
    html = client.get("/login").get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def post(client, url, data=None, follow=True):
    data = dict(data or {})
    data["csrf_token"] = token(client)
    return client.post(url, data=data, follow_redirects=follow)


def register(client, email="a@example.com"):
    return post(client, "/register", {"email": email, "password": "password123"})


def make_brand(client, name="Crew A", niche="AI automation for small businesses"):
    r = post(client, "/brands", {"name": name, "niche": niche})
    assert r.status_code == 200
    return int(re.search(r"/brands/(\d+)", r.request.path).group(1))


def test_requires_login(client):
    assert client.get("/").status_code == 302


def test_csrf_enforced(client):
    r = client.post("/register", data={"email": "x@example.com", "password": "password123"})
    assert r.status_code == 400


def test_full_pipeline_to_metrics(client):
    register(client)
    brand_id = make_brand(client)
    post(client, f"/brands/{brand_id}/channels", {"platform": "TikTok", "handle": "@crewtt"})
    post(client, f"/brands/{brand_id}/channels", {"platform": "Instagram", "handle": "crewig"})

    r = post(client, f"/brands/{brand_id}/runs")
    run_id = int(re.search(r"/runs/(\d+)", r.request.path).group(1))
    assert "Pick a topic" in r.get_data(as_text=True)

    r = post(client, f"/runs/{run_id}/pick", {"idx": "0"})
    assert "Script" in r.get_data(as_text=True)

    r = post(client, f"/runs/{run_id}/redo", {"note": "shorter"})
    assert r.status_code == 200

    r = post(client, f"/runs/{run_id}/approve")
    assert "Thumbnail and shot list" in r.get_data(as_text=True)

    r = post(client, f"/runs/{run_id}/approve")
    html = r.get_data(as_text=True)
    assert "Posts by platform" in html and "cap_TikTok" in html and "cap_Instagram" in html

    when = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M")
    r = post(client, f"/runs/{run_id}/approve", {"publish_at": when, "cap_TikTok": "My edited caption"})
    html = r.get_data(as_text=True)
    assert "2 post(s) scheduled" in html and "scheduled" in html

    r = post(client, f"/brands/{brand_id}/publish-due")
    html = r.get_data(as_text=True)
    assert "Published 2 post(s)" in html

    r = post(client, "/posts/1/metrics", {"views": "1200", "likes": "80", "followers": "15", "leads": "2"})
    html = r.get_data(as_text=True)
    assert "1200" in html and "15" in html


def test_daily_cap(client):
    register(client)
    brand_id = make_brand(client)
    post(client, f"/brands/{brand_id}/channels", {"platform": "TikTok", "handle": "capped"})
    when = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=2)).strftime("%Y-%m-%dT09:00")
    for i in range(4):
        r = post(client, f"/brands/{brand_id}/runs")
        run_id = int(re.search(r"/runs/(\d+)", r.request.path).group(1))
        post(client, f"/runs/{run_id}/pick", {"idx": "0"})
        post(client, f"/runs/{run_id}/approve")
        post(client, f"/runs/{run_id}/approve")
        r = post(client, f"/runs/{run_id}/approve", {"publish_at": when})
        if i < 3:
            assert "scheduled" in r.get_data(as_text=True)
        else:
            assert "already has 3 posts" in r.get_data(as_text=True)


def test_isolation_between_users(client):
    register(client, "one@example.com")
    brand_id = make_brand(client)
    post(client, "/logout")
    register(client, "two@example.com")
    assert client.get(f"/brands/{brand_id}").status_code == 404
    assert post(client, f"/brands/{brand_id}/delete", follow=False).status_code == 404


def test_run_needs_channel(client):
    register(client)
    brand_id = make_brand(client)
    r = post(client, f"/brands/{brand_id}/runs")
    assert "Add at least one channel" in r.get_data(as_text=True)
