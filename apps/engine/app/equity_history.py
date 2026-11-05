"""Ring buffer of paper equity snapshots for session charts."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Literal

EquityRange = Literal["1h", "6h", "24h", "1d", "1w", "1mo", "1y", "session", "all"]

_RANGE_DELTA: dict[str, timedelta | None] = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "1d": timedelta(days=1),
    "1w": timedelta(days=7),
    "1mo": timedelta(days=30),
    "1y": timedelta(days=365),
    "session": None,  # from session_started_at
    "all": None,
}

# Keep a dense ring for charts: ~2s while warming up, then ~5s steady.
_MAX_POINTS = 40_000
_MIN_INTERVAL_SEC = 5.0
_WARMUP_INTERVAL_SEC = 2.0
_WARMUP_UNTIL = 180
_MATERIAL_EQUITY = 0.15


class EquityHistory:
    def __init__(self, max_points: int = _MAX_POINTS) -> None:
        self._points: Deque[dict[str, Any]] = deque(maxlen=max_points)
        self.session_started_at: datetime = datetime.now(timezone.utc)
        self._last_append_at: datetime | None = None
        self._last_equity: float | None = None

    def reset_session(self) -> None:
        self.session_started_at = datetime.now(timezone.utc)
        self._points.clear()
        self._last_append_at = None
        self._last_equity = None

    def maybe_record(
        self,
        *,
        equity: float,
        cash: float,
        positions: int,
        pnl: float,
        peak_equity: float,
        force: bool = False,
    ) -> bool:
        now = datetime.now(timezone.utc)
        if not force and self._last_append_at is not None:
            elapsed = (now - self._last_append_at).total_seconds()
            moved = (
                self._last_equity is None
                or abs(float(equity) - float(self._last_equity)) >= _MATERIAL_EQUITY
            )
            min_interval = (
                _WARMUP_INTERVAL_SEC if len(self._points) < _WARMUP_UNTIL else _MIN_INTERVAL_SEC
            )
            # Always keep a heartbeat sample on the interval — not only on moves.
            if elapsed < min_interval and not (moved and elapsed >= 1.0):
                return False
        peak = float(peak_equity) if peak_equity > 0 else float(equity)
        dd = ((peak - float(equity)) / peak * 100.0) if peak > 0 else 0.0
        point = {
            "t": now.isoformat(),
            "equity": float(equity),
            "cash": float(cash),
            "positions": int(max(0, positions)),
            "pnl": float(pnl),
            "drawdown_pct": float(max(0.0, dd)),
        }
        self._points.append(point)
        self._last_append_at = now
        self._last_equity = float(equity)
        return True

    def restore(self, rows: list[Any], session_started_at: str | None = None) -> None:
        self._points.clear()
        if session_started_at:
            try:
                self.session_started_at = datetime.fromisoformat(
                    session_started_at.replace("Z", "+00:00")
                )
            except ValueError:
                pass
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                equity = float(row["equity"])
                cash = float(row["cash"]) if row.get("cash") is not None else equity
                self._points.append(
                    {
                        "t": str(row["t"]),
                        "equity": equity,
                        "cash": cash,
                        "positions": int(row.get("positions") or 0),
                        "pnl": float(row["pnl"]) if row.get("pnl") is not None else 0.0,
                        "drawdown_pct": (
                            float(row["drawdown_pct"]) if row.get("drawdown_pct") is not None else 0.0
                        ),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        if self._points:
            last = self._points[-1]
            try:
                self._last_append_at = datetime.fromisoformat(str(last["t"]).replace("Z", "+00:00"))
            except ValueError:
                self._last_append_at = None
            self._last_equity = float(last["equity"])

    def dump(self) -> list[dict[str, Any]]:
        return list(self._points)

    def query(self, range_key: str = "1h", max_out: int | None = None) -> dict[str, Any]:
        key = range_key if range_key in _RANGE_DELTA else "1h"
        now = datetime.now(timezone.utc)
        delta = _RANGE_DELTA[key]
        if key == "session":
            start = self.session_started_at
        elif key == "all" or delta is None:
            start = None
        else:
            start = now - delta

        # Keep charts dense: ~1 pt / 5s for 1h ≈ 720; cap longer windows.
        if max_out is None:
            if key in {"1h", "6h"}:
                max_out = 900
            elif key in {"24h", "1d"}:
                max_out = 720
            elif key in {"1w", "session"}:
                max_out = 600
            else:
                max_out = 480

        selected: list[dict[str, Any]] = []
        for point in self._points:
            try:
                ts = datetime.fromisoformat(str(point["t"]).replace("Z", "+00:00"))
            except ValueError:
                continue
            if start is not None and ts < start:
                continue
            selected.append(point)

        downsampled = _downsample(selected, max_out)
        return {
            "range": key,
            "session_started_at": self.session_started_at.isoformat(),
            "count": len(downsampled),
            "total_stored": len(self._points),
            "points": downsampled,
        }


def _downsample(points: list[dict[str, Any]], max_out: int) -> list[dict[str, Any]]:
    if len(points) <= max_out or max_out < 3:
        return points
    # Keep first/last + evenly spaced middle.
    out = [points[0]]
    step = (len(points) - 1) / (max_out - 1)
    for i in range(1, max_out - 1):
        idx = int(round(i * step))
        out.append(points[idx])
    out.append(points[-1])
    # Dedupe consecutive identical timestamps.
    deduped: list[dict[str, Any]] = []
    for p in out:
        if deduped and deduped[-1]["t"] == p["t"]:
            deduped[-1] = p
        else:
            deduped.append(p)
    return deduped
