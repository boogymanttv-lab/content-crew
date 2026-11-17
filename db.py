"""Database access for Content Crew.

Local runs use SQLite. Set DATABASE_URL (a postgres:// URL) to use Postgres, which is what
serverless hosts such as Vercel need, because their disk is not persistent.
"""
import os
import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    pw_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    niche TEXT NOT NULL,
    audience TEXT NOT NULL DEFAULT '',
    tone TEXT NOT NULL DEFAULT '',
    rules TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'English',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY,
    brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'dryrun',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    ideas_json TEXT,
    topic_json TEXT,
    script_json TEXT,
    design_json TEXT,
    pack_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    caption TEXT NOT NULL,
    hashtags TEXT NOT NULL DEFAULT '',
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    published_at TEXT,
    external_id TEXT,
    error TEXT,
    views BIGINT NOT NULL DEFAULT 0,
    likes BIGINT NOT NULL DEFAULT 0,
    followers_gained BIGINT NOT NULL DEFAULT 0,
    leads BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    UNIQUE(user_id, month)
);

CREATE INDEX IF NOT EXISTS idx_posts_due ON posts(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_brands_user ON brands(user_id);
"""


def _pg_schema():
    return (
        SCHEMA.replace("INTEGER PRIMARY KEY", "BIGSERIAL PRIMARY KEY")
        .replace("INTEGER NOT NULL REFERENCES", "BIGINT NOT NULL REFERENCES")
    )


class _Sqlite:
    def __init__(self, path):
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=()):
        return self.c.execute(sql, params)

    def executemany(self, sql, seq):
        return self.c.executemany(sql, seq)

    def insert(self, sql, params=()):
        return self.c.execute(sql, params).lastrowid

    def executescript(self, script):
        self.c.executescript(script)

    def commit(self):
        self.c.commit()

    def rollback(self):
        self.c.rollback()

    def close(self):
        self.c.close()


class _Postgres:
    def __init__(self, url):
        import psycopg
        from psycopg.rows import dict_row

        self.c = psycopg.connect(url, row_factory=dict_row, prepare_threshold=None)  # safe behind pgbouncer (Neon pooled URLs)

    @staticmethod
    def _q(sql):
        return sql.replace("?", "%s")

    def execute(self, sql, params=()):
        return self.c.execute(self._q(sql), params)

    def executemany(self, sql, seq):
        cur = self.c.cursor()
        cur.executemany(self._q(sql), seq)
        return cur

    def insert(self, sql, params=()):
        return self.c.execute(self._q(sql) + " RETURNING id", params).fetchone()["id"]

    def executescript(self, script):
        self.c.execute(_pg_schema() if script is SCHEMA else script)

    def commit(self):
        self.c.commit()

    def rollback(self):
        self.c.rollback()

    def close(self):
        self.c.close()


def _integrity_errors():
    errors = [sqlite3.IntegrityError]
    try:
        import psycopg

        errors.append(psycopg.errors.UniqueViolation)
    except ImportError:
        pass
    return tuple(errors)


IntegrityErrors = _integrity_errors()


def get_db():
    if "db" not in g:
        url = current_app.config.get("DATABASE_URL") or os.environ.get("DATABASE_URL", "")
        g.db = _Postgres(url) if url else _Sqlite(current_app.config["DATABASE"])
    return g.db


def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
