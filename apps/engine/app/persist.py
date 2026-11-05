"""Lightweight JSON persistence for paper wallet / journal across restarts."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
STATE_PATH = DATA_DIR / "paper_state.json"


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_state() -> dict[str, Any] | None:
    if not STATE_PATH.exists():
        return None
    try:
        raw = STATE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("persist load failed: %s", exc)
        return None


def save_state(payload: dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("persist save failed: %s", exc)


def clear_state() -> None:
    try:
        if STATE_PATH.exists():
            STATE_PATH.unlink()
    except Exception as exc:  # noqa: BLE001
        logger.warning("persist clear failed: %s", exc)


def parse_last_buys(raw: dict[str, Any] | None) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        ts = _parse_ts(str(value) if value is not None else None)
        if ts is not None:
            out[str(key)] = ts
    return out
