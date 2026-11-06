"""Paper fill model: decision mid → latency → adverse slip (+ optional impact).

Slippage lives in the fill price only — never re-applied when computing PnL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FillQuote:
    decision_ts: datetime
    decision_price: float
    fill_price: float
    latency_ms: int
    slip_bps: float
    impact_bps: float

    def to_meta(self) -> dict[str, Any]:
        return {
            "decision_ts": self.decision_ts.isoformat(),
            "decision_price": self.decision_price,
            "fill_price": self.fill_price,
            "latency_ms": self.latency_ms,
            "slip_bps": self.slip_bps,
            "impact_bps": self.impact_bps,
        }


def adverse_bps(side: str, slip_bps: float, impact_bps: float = 0.0) -> float:
    total = max(0.0, float(slip_bps)) + max(0.0, float(impact_bps))
    return total


def apply_adverse(side: str, mid: float, *, slip_bps: float, impact_bps: float = 0.0) -> float:
    if mid <= 0:
        return mid
    bps = adverse_bps(side, slip_bps, impact_bps) / 10_000.0
    if side == "buy":
        return mid * (1.0 + bps)
    return mid * (1.0 - bps)


def impact_bps_for_notional(
    notional: float,
    *,
    enabled: bool,
    equity: float,
    max_impact_bps: float = 5.0,
) -> float:
    """Prudent square-root impact stub. Off by default — returns 0."""
    if not enabled or notional <= 0 or equity <= 0 or max_impact_bps <= 0:
        return 0.0
    # ~max at 20% of equity; square-root taper below.
    ratio = min(1.0, notional / max(equity * 0.2, 1.0))
    return float(max_impact_bps) * (ratio**0.5)


def quote_fill(
    side: str,
    decision_price: float,
    *,
    slip_bps: float,
    latency_ms: int = 0,
    impact_bps: float = 0.0,
    fill_mid: float | None = None,
    decision_ts: datetime | None = None,
) -> FillQuote:
    """Build a fill from a decision mid. ``fill_mid`` may be a post-latency mark."""
    mid = float(fill_mid if fill_mid is not None else decision_price)
    fill = apply_adverse(side, mid, slip_bps=slip_bps, impact_bps=impact_bps)
    return FillQuote(
        decision_ts=decision_ts or _utc_now(),
        decision_price=float(decision_price),
        fill_price=float(fill),
        latency_ms=max(0, int(latency_ms)),
        slip_bps=float(slip_bps),
        impact_bps=float(impact_bps),
    )
