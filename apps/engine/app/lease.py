"""Single-row bot lease for exclusive trader ownership."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import db

_LEASE_ID = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: datetime | str | None) -> str | None:
    parsed = _as_utc(value)
    if parsed is None:
        return None
    return parsed.isoformat()


def read_lease() -> dict:
    """Public lease view for the API. Database errors propagate (fail-closed)."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT owner_id, heartbeat_at, expires_at, release_requested, version
                FROM bot_lease WHERE id=%s
                """,
                (_LEASE_ID,),
            )
            row = cur.fetchone()
    if row is None:
        return {
            "ok": False,
            "held": False,
            "owner_id": None,
            "heartbeat_at": None,
            "expires_at": None,
            "release_requested": False,
            "version": None,
            "error": "bot_lease row missing",
        }
    owner_id, heartbeat_at, expires_at, release_requested, version = row
    exp = _as_utc(expires_at)
    held = bool(owner_id) and exp is not None and exp >= _utc_now()
    return {
        "ok": True,
        "held": held,
        "owner_id": owner_id,
        "heartbeat_at": _iso(heartbeat_at),
        "expires_at": _iso(expires_at),
        "release_requested": bool(release_requested),
        "version": int(version) if version is not None else 0,
    }


class LeaseManager:
    """Postgres-backed mutex: at most one live owner with TTL heartbeat."""

    def __init__(self, owner_id: str) -> None:
        self._owner_id = owner_id
        self._version: int | None = None

    @property
    def owner_id(self) -> str:
        return self._owner_id

    def acquire(self, owner_id: str | None = None, ttl_sec: int = 20) -> bool:
        oid = owner_id if owner_id is not None else self._owner_id
        now = _utc_now()
        expires = now + timedelta(seconds=ttl_sec)
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE bot_lease
                    SET owner_id=%s, heartbeat_at=%s, expires_at=%s,
                        release_requested=%s, version=version+1
                    WHERE id=%s
                      AND (
                        owner_id IS NULL
                        OR expires_at < %s
                        OR owner_id=%s
                      )
                    RETURNING version
                    """,
                    (oid, now, expires, False, _LEASE_ID, now, oid),
                )
                row = cur.fetchone()
            if row is None:
                conn.rollback()
                return False
            conn.commit()
            self._version = int(row[0])
            return True

    def heartbeat(self, ttl_sec: int = 20) -> bool:
        now = _utc_now()
        expires = now + timedelta(seconds=ttl_sec)
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE bot_lease
                    SET heartbeat_at=%s, expires_at=%s, version=version+1
                    WHERE id=%s AND owner_id=%s AND expires_at >= %s
                    RETURNING version
                    """,
                    (now, expires, _LEASE_ID, self._owner_id, now),
                )
                row = cur.fetchone()
            if row is None:
                conn.rollback()
                return False
            conn.commit()
            self._version = int(row[0])
            return True

    def request_release(self) -> bool:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE bot_lease SET release_requested=%s
                    WHERE id=%s AND owner_id IS NOT NULL
                    RETURNING release_requested
                    """,
                    (True, _LEASE_ID),
                )
                row = cur.fetchone()
            if row is None:
                conn.rollback()
                return False
            conn.commit()
            return bool(row[0])

    def release(self) -> bool:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE bot_lease
                    SET owner_id=NULL, heartbeat_at=NULL, expires_at=NULL,
                        release_requested=%s, version=version+1
                    WHERE id=%s AND owner_id=%s
                    RETURNING version
                    """,
                    (False, _LEASE_ID, self._owner_id),
                )
                row = cur.fetchone()
            if row is None:
                conn.rollback()
                return False
            conn.commit()
            self._version = None
            return True

    def is_owner(self) -> bool:
        owner_id, expires_at, _, _ = self._read_row()
        if owner_id != self._owner_id or expires_at is None:
            return False
        exp = _as_utc(expires_at)
        if exp is None:
            return False
        return exp >= _utc_now()

    def release_requested(self) -> bool:
        _owner_id, _expires_at, requested, _version = self._read_row()
        return bool(requested)

    def _read_row(self) -> tuple:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT owner_id, expires_at, release_requested, version
                    FROM bot_lease WHERE id=%s
                    """,
                    (_LEASE_ID,),
                )
                row = cur.fetchone()
        if row is None:
            raise RuntimeError("bot_lease row missing")
        return row
