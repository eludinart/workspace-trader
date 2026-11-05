"""Test DB: real Postgres when DATABASE_URL/TEST_DATABASE_URL is set, else SQLite shim."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import pytest

from tests.db_sqlite import _SqliteConn, init_schema, sqlite_get_conn

_shared_sqlite: _SqliteConn | None = None


def _postgres_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or None


def _reset_sqlite(conn: _SqliteConn) -> None:
    tables = [
        "control_audit",
        "equity_points",
        "journal",
        "trades",
        "positions",
        "account",
        "runtime_meta",
        "bot_params",
    ]
    for table in tables:
        conn._conn.execute(f"DELETE FROM {table}")
    conn._conn.execute("DELETE FROM sqlite_sequence WHERE name='control_audit'")
    conn.commit()


@contextmanager
def _shared_sqlite_get_conn() -> Iterator[_SqliteConn]:
    global _shared_sqlite
    if _shared_sqlite is None:
        with sqlite_get_conn() as conn:
            _shared_sqlite = conn
            # keep connection open for process lifetime
            _shared_sqlite._hold = True  # type: ignore[attr-defined]
    yield _shared_sqlite


@pytest.fixture
def store_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    global _shared_sqlite
    url = _postgres_url()
    if url:
        monkeypatch.setenv("DATABASE_URL", url)
        from app.migrate import run_migrations

        run_migrations()
        with _truncate_postgres():
            yield
        return

    if _shared_sqlite is None:
        conn = _SqliteConn()
        init_schema(conn)
        _shared_sqlite = conn

    _reset_sqlite(_shared_sqlite)

    @contextmanager
    def _shim_get_conn():
        yield _shared_sqlite

    monkeypatch.setattr("app.db.get_conn", _shim_get_conn)
    monkeypatch.setattr("app.store.get_conn", _shim_get_conn)
    monkeypatch.setattr("app.migrate.get_conn", _shim_get_conn)
    yield


@contextmanager
def _truncate_postgres() -> Iterator[None]:
    from app.db import get_conn

    tables = [
        "control_audit",
        "equity_points",
        "journal",
        "trades",
        "positions",
        "account",
        "runtime_meta",
        "bot_params",
    ]
    with get_conn() as conn:
        with conn.cursor() as cur:
            for table in tables:
                cur.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")
        conn.commit()
    yield
