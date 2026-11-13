"""SQLite-backed connection shim for Store SQL tests (no local Postgres)."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from psycopg.types.json import Json

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  id TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bot_lease (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  owner_id TEXT,
  heartbeat_at TEXT,
  expires_at TEXT,
  release_requested INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO bot_lease (id) VALUES (1);

CREATE TABLE IF NOT EXISTS account (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  mode TEXT NOT NULL DEFAULT 'paper',
  cash REAL NOT NULL,
  initial_balance REAL NOT NULL,
  realized_pnl REAL NOT NULL DEFAULT 0,
  peak_equity REAL NOT NULL,
  killed INTEGER NOT NULL DEFAULT 0,
  kill_reason TEXT,
  session_started_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS positions (
  symbol TEXT PRIMARY KEY,
  qty REAL NOT NULL,
  avg_entry REAL NOT NULL,
  stop_pct REAL,
  take_pct REAL,
  exit_meta TEXT NOT NULL DEFAULT '{}',
  cost_basis REAL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trades (
  id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  qty REAL NOT NULL,
  price REAL NOT NULL,
  fee REAL NOT NULL DEFAULT 0,
  ts TEXT NOT NULL,
  realized_pnl REAL NOT NULL DEFAULT 0,
  note TEXT,
  decision_ts TEXT,
  decision_price REAL,
  latency_ms INTEGER,
  slip_bps REAL,
  impact_bps REAL,
  invested_quote REAL,
  avg_entry_ref REAL,
  cost_basis_closed REAL,
  reference_ts TEXT,
  meta_complete INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS signal_observations (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  symbol TEXT NOT NULL,
  as_of TEXT NOT NULL,
  ingested_at TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  confidence_tier TEXT NOT NULL DEFAULT 'hypothesis',
  source TEXT NOT NULL DEFAULT 'stub'
);

CREATE TABLE IF NOT EXISTS journal (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_points (
  ts TEXT NOT NULL,
  equity REAL NOT NULL,
  cash REAL,
  positions INTEGER,
  pnl REAL,
  drawdown_pct REAL,
  PRIMARY KEY (ts)
);

CREATE TABLE IF NOT EXISTS bot_params (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  params TEXT NOT NULL DEFAULT '{}',
  alerts TEXT NOT NULL DEFAULT '{}',
  llm TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO bot_params (id) VALUES (1);

CREATE TABLE IF NOT EXISTS runtime_meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  last_buy_at TEXT NOT NULL DEFAULT '{}',
  signal_history TEXT NOT NULL DEFAULT '[]',
  migrated_at TEXT,
  reset_requested INTEGER NOT NULL DEFAULT 0,
  reset_balance REAL,
  close_requests TEXT NOT NULL DEFAULT '[]',
  daily_report_pushed_on TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO runtime_meta (id) VALUES (1);

CREATE TABLE IF NOT EXISTS push_subscriptions (
  endpoint TEXT PRIMARY KEY,
  keys TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS control_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}'
);
"""


def _adapt_sql_for_sqlite(sql: str) -> str:
    text = sql
    text = text.replace("CURRENT_TIMESTAMP", "datetime('now')")
    text = text.replace("now()", "datetime('now')")
    text = re.sub(r"\bEXCLUDED\b\.", "excluded.", text)
    # Postgres advisory lock / cast helpers — no-ops or plain binds on SQLite.
    text = re.sub(
        r"SELECT\s+pg_advisory_xact_lock\s*\(\s*\?\s*\)",
        "SELECT 1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"SELECT\s+pg_advisory_xact_lock\s*\(\s*%s\s*\)",
        "SELECT 1",
        text,
        flags=re.IGNORECASE,
    )
    text = text.replace("%s::date", "%s")
    text = text.replace("?::date", "?")
    return text


def _unwrap_param(value: Any) -> Any:
    if isinstance(value, Json):
        wrapped = getattr(value, "obj", None)
        if wrapped is None:
            wrapped = getattr(value, "_obj", value)
        return json.dumps(wrapped)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    return value


def _decode_row_value(value: Any, json_columns: bool = False) -> Any:
    if json_columns and isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, str) and _looks_like_iso_ts(value):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value


def _looks_like_iso_ts(text: str) -> bool:
    return len(text) >= 19 and text[4] == "-" and "T" in text


class _SqliteCursor:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> _SqliteCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        if "pg_advisory_xact_lock" in sql.lower():
            # Postgres-only mutex — no-op under the SQLite test shim.
            self._rows = [(1,)]
            return
        adapted = _adapt_sql_for_sqlite(sql).replace("%s", "?")
        # SQLite UPSERT syntax uses excluded.* not EXCLUDED.*
        adapted = adapted.replace("EXCLUDED.", "excluded.")
        bound = tuple(_unwrap_param(p) for p in (params or ()))
        cur = self._conn.execute(adapted, bound)
        if cur.description:
            decoded: list[tuple[Any, ...]] = []
            for row in cur.fetchall():
                items = []
                for idx, col in enumerate(cur.description):
                    val = row[idx]
                    col_name = col[0]
                    is_json = col_name in {
                        "exit_meta",
                        "payload",
                        "params",
                        "alerts",
                        "llm",
                        "last_buy_at",
                        "signal_history",
                    }
                    if col_name == "killed":
                        val = bool(val)
                    items.append(_decode_row_value(val, json_columns=is_json))
                decoded.append(tuple(items))
            self._rows = decoded
        else:
            self._rows = []

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _SqliteConn:
    def __init__(self) -> None:
        # TestClient handles requests off the thread that opened this connection.
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def cursor(self) -> _SqliteCursor:
        return _SqliteCursor(self._conn)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def init_schema(conn: _SqliteConn) -> None:
    conn._conn.executescript(SQLITE_SCHEMA)
    conn.commit()


@contextmanager
def sqlite_get_conn() -> Iterator[_SqliteConn]:
    conn = _SqliteConn()
    try:
        init_schema(conn)
        yield conn
    finally:
        conn.close()
