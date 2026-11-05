"""Bot lease: exclusive acquire, expiry steal, release_requested."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.lease import LeaseManager


def _reset_bot_lease() -> None:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE bot_lease
                SET owner_id=NULL, heartbeat_at=NULL, expires_at=NULL,
                    release_requested=%s, version=0
                WHERE id=%s
                """,
                (False, 1),
            )
        conn.commit()


def _lease_row() -> tuple:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT owner_id, expires_at, release_requested, version
                FROM bot_lease WHERE id=%s
                """,
                (1,),
            )
            row = cur.fetchone()
    assert row is not None
    return row


@pytest.fixture
def lease_db(store_db: None) -> None:
    _reset_bot_lease()


def test_acquire_exclusivity_second_owner_fails(lease_db: None) -> None:
    a = LeaseManager("owner-a")
    b = LeaseManager("owner-b")

    assert a.acquire() is True
    assert b.acquire() is False
    assert a.is_owner() is True
    assert b.is_owner() is False

    owner_id, _, _, _ = _lease_row()
    assert owner_id == "owner-a"


def test_acquire_steals_when_expired(lease_db: None) -> None:
    a = LeaseManager("owner-a")
    b = LeaseManager("owner-b")

    assert a.acquire(ttl_sec=20) is True

    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bot_lease SET expires_at=%s WHERE id=%s",
                (past, 1),
            )
        conn.commit()

    assert b.acquire() is True
    assert b.is_owner() is True
    assert a.is_owner() is False


def test_request_release_sets_flag_while_holder_still_owner(lease_db: None) -> None:
    holder = LeaseManager("owner-a")
    challenger = LeaseManager("owner-b")

    assert holder.acquire() is True
    assert challenger.request_release() is True

    _, _, release_requested, _ = _lease_row()
    assert bool(release_requested)
    assert holder.is_owner() is True

    assert holder.release() is True
    _, _, release_requested, _ = _lease_row()
    assert not bool(release_requested)


def test_heartbeat_extends_lease_for_owner_only(lease_db: None) -> None:
    a = LeaseManager("owner-a")
    b = LeaseManager("owner-b")

    assert a.acquire(ttl_sec=5) is True
    _, expires_before, _, _ = _lease_row()

    assert a.heartbeat(ttl_sec=60) is True
    _, expires_after, _, _ = _lease_row()
    assert expires_after > expires_before

    assert b.heartbeat(ttl_sec=60) is False
