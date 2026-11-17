"""Vercel entry point. Vercel's disk is not persistent, so DATABASE_URL (Postgres) and SECRET_KEY are required."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402

if os.environ.get("VERCEL") and not os.environ.get("DATABASE_URL"):
    raise RuntimeError("Set DATABASE_URL (a Postgres connection string) in the Vercel project settings.")

app = create_app()
