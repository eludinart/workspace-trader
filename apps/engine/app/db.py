from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def require_database_url() -> str:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required")
    return DATABASE_URL


@contextmanager
def get_conn():
    with psycopg.connect(require_database_url(), autocommit=False) as conn:
        yield conn
