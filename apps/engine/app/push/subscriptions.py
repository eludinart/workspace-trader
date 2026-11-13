"""Persist and send Web Push subscriptions."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from psycopg.types.json import Json

from app.db import get_conn
from app.push.vapid import vapid_private_key_configured, vapid_public_key

logger = logging.getLogger(__name__)


def upsert_subscription(endpoint: str, keys: dict[str, Any]) -> None:
    ep = (endpoint or "").strip()
    if not ep:
        raise ValueError("endpoint required")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO push_subscriptions (endpoint, keys, created_at, updated_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (endpoint) DO UPDATE SET
                  keys = EXCLUDED.keys,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (ep, Json(keys or {})),
            )
        conn.commit()


def delete_subscription(endpoint: str) -> bool:
    ep = (endpoint or "").strip()
    if not ep:
        return False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (ep,))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def list_subscriptions() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT endpoint, keys FROM push_subscriptions")
            rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for endpoint, keys in rows:
        k = keys if isinstance(keys, dict) else {}
        out.append({"endpoint": endpoint, "keys": k})
    return out


def _vapid_claims() -> dict[str, str]:
    subject = (os.getenv("VAPID_SUBJECT") or "mailto:ops@eludein.art").strip()
    return {"sub": subject}


def send_web_push(title: str, body: str, *, url: str = "/") -> dict[str, Any]:
    """Send JSON payload to all stored subscriptions. Drops gone endpoints."""
    pub = vapid_public_key()
    priv = (os.getenv("VAPID_PRIVATE_KEY") or "").strip()
    if not pub or not priv or not vapid_private_key_configured():
        return {"ok": False, "error": "vapid_not_configured", "sent": 0}

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return {"ok": False, "error": "pywebpush_missing", "sent": 0}

    payload = json.dumps({"title": title, "body": body, "url": url})
    sent = 0
    failed = 0
    removed = 0
    for sub in list_subscriptions():
        endpoint = str(sub.get("endpoint") or "")
        keys = sub.get("keys") if isinstance(sub.get("keys"), dict) else {}
        subscription_info = {
            "endpoint": endpoint,
            "keys": {
                "p256dh": str(keys.get("p256dh") or ""),
                "auth": str(keys.get("auth") or ""),
            },
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=priv,
                vapid_claims=_vapid_claims(),
            )
            sent += 1
        except WebPushException as exc:
            failed += 1
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning("web push failed endpoint=%s status=%s: %s", endpoint[:48], status, exc)
            if status in (404, 410):
                if delete_subscription(endpoint):
                    removed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("web push error: %s", exc)

    return {"ok": sent > 0 or failed == 0, "sent": sent, "failed": failed, "removed": removed}
