"""VAPID env readers for Web Push (point 19)."""

from __future__ import annotations

import os
from typing import Any


def vapid_public_key() -> str | None:
    raw = (os.getenv("VAPID_PUBLIC_KEY") or os.getenv("NEXT_PUBLIC_VAPID_PUBLIC_KEY") or "").strip()
    return raw or None


def vapid_private_key_configured() -> bool:
    """True if private key is present in env — never return the secret value."""
    return bool((os.getenv("VAPID_PRIVATE_KEY") or "").strip())


def web_push_status() -> dict[str, Any]:
    return {
        "public_key_configured": vapid_public_key() is not None,
        "private_key_configured": vapid_private_key_configured(),
        "note": (
            "HTTPS + domaine public requis. Clé privée uniquement en secret Coolify/env. "
            "Pas de Telegram."
        ),
    }
