"""Paper fill model: legacy slip + execution-twin L2 walk.

Slippage lives in the fill price only — never re-applied when computing PnL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence


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
    # Twin extras (defaults keep legacy callers working).
    source: str = "legacy_slip"
    filled_notional: float = 0.0
    unfilled_notional: float = 0.0
    levels_consumed: int = 0
    book_age_ms: int = 0
    rejected: bool = False
    reject_reason: str = ""

    def to_meta(self) -> dict[str, Any]:
        return {
            "decision_ts": self.decision_ts.isoformat(),
            "decision_price": self.decision_price,
            "fill_price": self.fill_price,
            "latency_ms": self.latency_ms,
            "slip_bps": self.slip_bps,
            "impact_bps": self.impact_bps,
            "source": self.source,
            "filled_notional": self.filled_notional,
            "unfilled_notional": self.unfilled_notional,
            "levels_consumed": self.levels_consumed,
            "book_age_ms": self.book_age_ms,
            "rejected": self.rejected,
            "reject_reason": self.reject_reason or None,
        }


def adverse_bps(side: str, slip_bps: float, impact_bps: float = 0.0) -> float:
    del side
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
    source: str = "legacy_slip",
) -> FillQuote:
    """Build a fill from a decision mid. ``fill_mid`` may be a post-latency mark."""
    mid = float(fill_mid if fill_mid is not None else decision_price)
    fill = apply_adverse(side, mid, slip_bps=slip_bps, impact_bps=impact_bps)
    notional_est = abs(fill)  # placeholder; callers often overwrite filled_notional
    return FillQuote(
        decision_ts=decision_ts or _utc_now(),
        decision_price=float(decision_price),
        fill_price=float(fill),
        latency_ms=max(0, int(latency_ms)),
        slip_bps=float(slip_bps),
        impact_bps=float(impact_bps),
        source=source,
        filled_notional=float(notional_est),
        unfilled_notional=0.0,
        levels_consumed=0,
        book_age_ms=0,
        rejected=False,
    )


def walk_real_book(
    side: str,
    *,
    bids: Sequence[tuple[float, float] | BookLevel],
    asks: Sequence[tuple[float, float] | BookLevel],
    notional: float,
    decision_price: float,
    latency_ms: int = 0,
    min_fill_notional: float = 15.0,
    book_age_ms: int = 0,
    decision_ts: datetime | None = None,
) -> FillQuote:
    """Walk a real Coinbase L2 snapshot (taker-style). Fee is applied separately in wallet.

    ``decision_price`` should be the mid (or last) at decision time for slip reporting.
    """
    levels_raw = asks if side == "buy" else bids
    levels: list[BookLevel] = []
    for row in levels_raw:
        if isinstance(row, BookLevel):
            levels.append(row)
        else:
            price, size = float(row[0]), float(row[1])
            if price > 0 and size > 0:
                levels.append(BookLevel(price=price, size=size))

    remaining = max(0.0, float(notional))
    spent = 0.0
    qty = 0.0
    consumed = 0
    for lvl in levels:
        if remaining <= 0:
            break
        level_notional = lvl.price * lvl.size
        take = min(remaining, level_notional)
        if take <= 0:
            continue
        take_qty = take / lvl.price
        spent += take
        qty += take_qty
        remaining -= take
        consumed += 1

    mid = float(decision_price)
    ts = decision_ts or _utc_now()
    if qty <= 0 or spent < float(min_fill_notional):
        return FillQuote(
            decision_ts=ts,
            decision_price=mid,
            fill_price=0.0,
            latency_ms=max(0, int(latency_ms)),
            slip_bps=0.0,
            impact_bps=0.0,
            source="coinbase_l2",
            filled_notional=0.0,
            unfilled_notional=max(0.0, float(notional)),
            levels_consumed=consumed,
            book_age_ms=max(0, int(book_age_ms)),
            rejected=True,
            reject_reason="insufficient_depth",
        )

    avg = spent / qty
    if mid > 0:
        if side == "buy":
            walked_bps = max(0.0, (avg / mid - 1.0) * 10_000.0)
        else:
            walked_bps = max(0.0, (1.0 - avg / mid) * 10_000.0)
    else:
        walked_bps = 0.0

    return FillQuote(
        decision_ts=ts,
        decision_price=mid,
        fill_price=float(avg),
        latency_ms=max(0, int(latency_ms)),
        slip_bps=float(walked_bps),
        impact_bps=0.0,
        source="coinbase_l2",
        filled_notional=float(spent),
        unfilled_notional=float(remaining),
        levels_consumed=consumed,
        book_age_ms=max(0, int(book_age_ms)),
        rejected=False,
    )
