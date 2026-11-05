"""Apply SQL migrations from app/migrations/*.sql."""

from __future__ import annotations

import logging
from pathlib import Path

from app.db import get_conn

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _bootstrap_schema_migrations(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              id TEXT PRIMARY KEY,
              applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
    conn.commit()


def _applied_migration_ids(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def run_migrations() -> list[str]:
    """Run pending migrations in sorted filename order. Returns applied migration ids."""
    if not MIGRATIONS_DIR.is_dir():
        raise RuntimeError(f"migrations directory missing: {MIGRATIONS_DIR}")

    paths = sorted(MIGRATIONS_DIR.glob("*.sql"))
    applied_now: list[str] = []

    with get_conn() as conn:
        _bootstrap_schema_migrations(conn)
        already = _applied_migration_ids(conn)

        for path in paths:
            migration_id = path.stem
            if migration_id in already:
                logger.debug("skip migration %s (already applied)", migration_id)
                continue

            sql = path.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (id) VALUES (%s)",
                    (migration_id,),
                )
            conn.commit()
            already.add(migration_id)
            applied_now.append(migration_id)
            logger.info("applied migration %s", migration_id)

    return applied_now


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    applied = run_migrations()
    if applied:
        for mid in applied:
            print(f"Applied {mid}")
    else:
        print("No pending migrations")


if __name__ == "__main__":
    main()
