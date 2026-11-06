"""Point 18 — spot/derivatives super-opportunity stub (disabled; sizing under cap only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


HARD_RISK_CAP = 0.02  # 2% capital/trade — never exceeded here


@dataclass(frozen=True)
class SuperOppResult:
    enabled: bool
    detected: bool
    score: float | None
    size_mult: float
    under_risk_cap: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "detected": self.detected,
            "score": self.score,
            "size_mult": self.size_mult,
            "under_risk_cap": self.under_risk_cap,
            "reason": self.reason,
            "hard_risk_cap_pct": HARD_RISK_CAP,
        }


def detect_super_opportunity(
    *,
    enabled: bool,
    spot_vs_perp_basis_bps: float | None = None,
    confluence_score: float | None = None,
    risk_per_trade_pct: float = 0.012,
) -> SuperOppResult:
    if not enabled:
        return SuperOppResult(
            enabled=False,
            detected=False,
            score=None,
            size_mult=1.0,
            under_risk_cap=True,
            reason="super_opp_enabled=false",
        )
    if spot_vs_perp_basis_bps is None:
        return SuperOppResult(
            enabled=True,
            detected=False,
            score=None,
            size_mult=1.0,
            under_risk_cap=True,
            reason="stub: pas de basis spot/perp fourni",
        )
    # Divergence heuristic: large negative funding/basis + positive confluence.
    score = abs(float(spot_vs_perp_basis_bps)) / 100.0
    if confluence_score is not None:
        score = 0.5 * score + 0.5 * max(0.0, float(confluence_score))
    detected = score >= 0.4 and float(spot_vs_perp_basis_bps) < -5.0
    # Mild oversize only under hard 2% cap.
    mult = 1.15 if detected else 1.0
    projected = float(risk_per_trade_pct) * mult
    if projected > HARD_RISK_CAP:
        mult = HARD_RISK_CAP / max(float(risk_per_trade_pct), 1e-9)
        projected = float(risk_per_trade_pct) * mult
    return SuperOppResult(
        enabled=True,
        detected=detected,
        score=float(score),
        size_mult=float(mult),
        under_risk_cap=projected <= HARD_RISK_CAP + 1e-12,
        reason="ok" if detected else "divergence insuffisante (stub)",
    )
