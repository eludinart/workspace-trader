"""Paper fill idempotency keys — skip duplicate decision retries."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from app.wallet import Trade


def decision_fill_key(
    *,
    decision_ts: datetime | None,
    symbol: str,
    side: str,
    qty: float,
) -> str | None:
    if decision_ts is None:
        return None
    ts = decision_ts.isoformat()
    qty_r = f"{float(qty):.8f}"
    return f"{ts}|{symbol.strip().upper()}|{side.strip().lower()}|{qty_r}"


def trade_fill_key(trade: Trade) -> str | None:
    return decision_fill_key(
        decision_ts=trade.decision_ts,
        symbol=trade.symbol,
        side=trade.side,
        qty=trade.qty,
    )


def has_fill_key(trades: Iterable[Trade], key: str | None) -> bool:
    if not key:
        return False
    for t in trades:
        if trade_fill_key(t) == key:
            return True
    return False
