"""Execution-twin fidelity tracking and go-live gate (shadow only)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class FillSample:
    ts: datetime
    symbol: str
    side: str
    source: str
    slip_bps: float
    impact_bps: float
    filled_notional: float
    unfilled_notional: float
    rejected: bool
    fee_bps: float


@dataclass
class FidelityTracker:
    """In-memory ring of twin fills for the go/no-go gate."""

    min_fills: int = 100
    min_days: float = 14.0
    max_unfilled_rate: float = 0.10
    samples: deque[FillSample] = field(default_factory=lambda: deque(maxlen=2_000))
    first_twin_at: datetime | None = None

    def record(self, sample: FillSample) -> None:
        if sample.source == "coinbase_l2" and self.first_twin_at is None:
            self.first_twin_at = sample.ts
        self.samples.append(sample)

    def _twin_samples(self) -> list[FillSample]:
        return [s for s in self.samples if s.source == "coinbase_l2"]

    def report(
        self,
        *,
        twin_enabled: bool,
        fee_tier: str,
        taker_fee_bps: float,
    ) -> dict[str, Any]:
        twin = self._twin_samples()
        n = len(twin)
        now = _utc_now()
        days = 0.0
        if self.first_twin_at is not None:
            days = max(0.0, (now - self.first_twin_at).total_seconds() / 86_400.0)

        slips = [s.slip_bps + s.impact_bps for s in twin if not s.rejected]
        median_slip = float(sorted(slips)[len(slips) // 2]) if slips else 0.0
        rejects = sum(1 for s in twin if s.rejected)
        partials = sum(
            1 for s in twin if (not s.rejected) and s.unfilled_notional > 1e-9
        )
        unfilled_rate = (rejects + partials) / n if n else 0.0
        # Round-trip friction estimate: 2 × taker + median book slip (one-way ×2).
        rt_friction_bps = 2.0 * float(taker_fee_bps) + 2.0 * median_slip
        theoretical_min_rt = 2.0 * float(taker_fee_bps)

        reasons: list[str] = []
        if not twin_enabled:
            reasons.append("execution_twin_disabled")
        if n < self.min_fills:
            reasons.append(f"fills_below_min:{n}/{self.min_fills}")
        if days < self.min_days:
            reasons.append(f"days_below_min:{days:.1f}/{self.min_days}")
        if twin_enabled and n > 0 and rt_friction_bps + 1e-9 < theoretical_min_rt:
            reasons.append("friction_below_taker_floor")
        if n >= 20 and unfilled_rate > self.max_unfilled_rate:
            reasons.append(f"unfilled_rate_high:{unfilled_rate:.2%}")

        go_live_ready = twin_enabled and len(reasons) == 0

        return {
            "execution_mode": "twin_l2" if twin_enabled else "legacy_slip",
            "fee_tier": fee_tier,
            "taker_fee_bps": float(taker_fee_bps),
            "twin_fill_count": n,
            "days_tracked": round(days, 2),
            "median_book_slip_bps": round(median_slip, 3),
            "round_trip_friction_bps_est": round(rt_friction_bps, 3),
            "theoretical_min_rt_bps": round(theoretical_min_rt, 3),
            "reject_count": rejects,
            "partial_count": partials,
            "unfilled_rate": round(unfilled_rate, 4),
            "go_live_ready": go_live_ready,
            "block_reasons": reasons,
            "gate": {
                "min_fills": self.min_fills,
                "min_days": self.min_days,
                "max_unfilled_rate": self.max_unfilled_rate,
            },
            "message_fr": (
                "Prêt à considérer un micro-live (gate verte)."
                if go_live_ready
                else "Pas prêt pour l’argent réel — "
                + (", ".join(reasons) if reasons else "en attente")
            ),
        }
