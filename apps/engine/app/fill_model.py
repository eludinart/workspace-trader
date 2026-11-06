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


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class SimulatedBook:
    """Synthetic L2 book around mid — labeled simulation, not a live exchange feed."""

    mid: float
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    depth_levels: int
    label: str = "simulated_book:hypothesis"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mid": self.mid,
            "bids": [{"price": b.price, "size": b.size} for b in self.bids],
            "asks": [{"price": a.price, "size": a.size} for a in self.asks],
            "depth_levels": self.depth_levels,
            "label": self.label,
            "confidence_tier": "hypothesis",
        }


def simulate_order_book(
    mid: float,
    *,
    depth_levels: int = 5,
    tick_bps: float = 1.0,
    base_size: float = 0.05,
    size_growth: float = 1.35,
) -> SimulatedBook:
    """Build a symmetric depth ladder for paper latency/impact walks."""
    mid_f = float(mid)
    n = max(1, int(depth_levels))
    tick = mid_f * (max(0.1, float(tick_bps)) / 10_000.0)
    bids: list[BookLevel] = []
    asks: list[BookLevel] = []
    size = max(1e-8, float(base_size))
    for i in range(1, n + 1):
        bids.append(BookLevel(price=mid_f - tick * i, size=size))
        asks.append(BookLevel(price=mid_f + tick * i, size=size))
        size *= float(size_growth)
    return SimulatedBook(
        mid=mid_f,
        bids=tuple(bids),
        asks=tuple(asks),
        depth_levels=n,
    )


def walk_book_fill(
    side: str,
    mid: float,
    notional: float,
    *,
    latency_ms: int = 0,
    depth_levels: int = 5,
    base_slip_bps: float = 5.0,
) -> FillQuote:
    """Fill by walking simulated depth; latency widens effective slip slightly.

    Still paper-only. Extra slip from depth + latency is capped and additive to
    ``base_slip_bps``. Impact is the walked adverse vs mid expressed in bps.
    """
    book = simulate_order_book(mid, depth_levels=depth_levels)
    levels = book.asks if side == "buy" else book.bids
    remaining = max(0.0, float(notional))
    spent = 0.0
    qty = 0.0
    for lvl in levels:
        if remaining <= 0 or lvl.price <= 0:
            break
        level_notional = lvl.price * lvl.size
        take = min(remaining, level_notional)
        take_qty = take / lvl.price
        spent += take
        qty += take_qty
        remaining -= take
    if qty <= 0:
        # Empty book fallback — pure adverse base slip.
        return quote_fill(
            side,
            mid,
            slip_bps=base_slip_bps,
            latency_ms=latency_ms,
            impact_bps=0.0,
        )
    avg = spent / qty
    # Latency: each 100ms adds ~0.5 bps adverse (cap 5 bps).
    latency_extra = min(5.0, max(0, int(latency_ms)) / 100.0 * 0.5)
    # Convert walked distance to bps vs mid.
    if side == "buy":
        walked_bps = max(0.0, (avg / mid - 1.0) * 10_000.0) if mid > 0 else 0.0
    else:
        walked_bps = max(0.0, (1.0 - avg / mid) * 10_000.0) if mid > 0 else 0.0
    impact = max(0.0, walked_bps - float(base_slip_bps))
    return quote_fill(
        side,
        mid,
        slip_bps=float(base_slip_bps) + latency_extra,
        latency_ms=latency_ms,
        impact_bps=impact,
        fill_mid=mid,
    )


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
