"""Session / range performance metrics for paper trading."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from app.wallet import Trade

PerfRange = Literal["session", "7d", "30d", "all"]


def _as_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _range_start(
    range_key: str,
    *,
    now: datetime,
    session_started_at: datetime | None,
) -> datetime | None:
    key = (range_key or "session").strip().lower()
    if key == "all":
        return None
    if key == "session":
        return _as_utc(session_started_at) or now - timedelta(days=1)
    if key == "7d":
        return now - timedelta(days=7)
    if key == "30d":
        return now - timedelta(days=30)
    return _as_utc(session_started_at)


def build_performance_metrics(
    *,
    trades: list[Trade],
    equity_points: list[dict[str, Any]],
    fidelity: dict[str, Any] | None,
    range_key: str = "session",
    session_started_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now_utc = _as_utc(now) or datetime.now(timezone.utc)
    start = _range_start(range_key, now=now_utc, session_started_at=session_started_at)

    def in_range(ts: datetime | None) -> bool:
        u = _as_utc(ts)
        if u is None:
            return False
        if start is None:
            return True
        return u >= start

    sells = [t for t in trades if t.side == "sell" and in_range(t.ts)]
    all_in = [t for t in trades if in_range(t.ts)]
    wins = [t for t in sells if float(t.realized_pnl) > 0]
    losses = [t for t in sells if float(t.realized_pnl) < 0]
    flat = [t for t in sells if float(t.realized_pnl) == 0]
    win_sum = sum(float(t.realized_pnl) for t in wins)
    loss_sum = sum(float(t.realized_pnl) for t in losses)  # negative
    fees = sum(float(t.fee) for t in all_in)
    net = sum(float(t.realized_pnl) for t in sells)
    closed = len(wins) + len(losses)
    win_rate = (len(wins) / closed) if closed else 0.0
    avg_win = (win_sum / len(wins)) if wins else 0.0
    avg_loss = (loss_sum / len(losses)) if losses else 0.0
    profit_factor = (win_sum / abs(loss_sum)) if loss_sum < 0 else (None if not wins else float("inf"))

    eq = []
    for p in equity_points:
        if not isinstance(p, dict):
            continue
        try:
            ts = datetime.fromisoformat(str(p.get("t")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if in_range(ts):
            eq.append(p)
    max_dd = 0.0
    for p in eq:
        try:
            max_dd = max(max_dd, float(p.get("drawdown_pct") or 0))
        except (TypeError, ValueError):
            continue

    days = 1.0
    if start is not None:
        days = max(1.0, (now_utc - start).total_seconds() / 86_400.0)
    elif all_in:
        first = min(_as_utc(t.ts) or now_utc for t in all_in)
        days = max(1.0, (now_utc - first).total_seconds() / 86_400.0)

    fid = fidelity or {}
    return {
        "range": range_key,
        "window_start": start.isoformat() if start else None,
        "closed_roundtrips": closed,
        "wins": len(wins),
        "losses": len(losses),
        "flats": len(flat),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor if profit_factor != float("inf") else None,
        "profit_factor_infinite": profit_factor == float("inf"),
        "net_realized_pnl": net,
        "fees_total": fees,
        "max_drawdown_pct": max_dd,
        "trades_per_day": len(all_in) / days,
        "trade_count": len(all_in),
        "unfilled_rate": float(fid.get("unfilled_rate") or 0),
        "reject_count": int(fid.get("reject_count") or 0),
        "partial_count": int(fid.get("partial_count") or 0),
    }
