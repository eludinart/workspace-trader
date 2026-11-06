"""Point 16 — fractional Kelly evaluation only (never sizes until sample + OOS OK)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class KellyEval:
    enabled: bool
    active: bool
    fraction: float | None
    sample_n: int
    reason: str
    suggested_size_mult: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "active": self.active,
            "fraction": self.fraction,
            "sample_n": self.sample_n,
            "reason": self.reason,
            "suggested_size_mult": self.suggested_size_mult,
            "applies_to_sizing": False,  # hard: eval-only until explicitly wired
        }


def evaluate_kelly(
    trade_pnls: Sequence[float],
    *,
    enabled: bool,
    min_sample: int = 200,
    kelly_fraction: float = 0.25,
    vol_ratio_vs_baseline: float | None = None,
    max_position_corr: float | None = None,
) -> KellyEval:
    n = len(trade_pnls)
    if not enabled:
        return KellyEval(
            enabled=False,
            active=False,
            fraction=None,
            sample_n=n,
            reason="kelly_enabled=false",
        )
    if n < min_sample:
        return KellyEval(
            enabled=True,
            active=False,
            fraction=None,
            sample_n=n,
            reason=f"échantillon insuffisant ({n}/{min_sample}) — sizing inchangé",
        )
    wins = [p for p in trade_pnls if p > 0]
    losses = [abs(p) for p in trade_pnls if p < 0]
    if not wins or not losses:
        return KellyEval(
            enabled=True,
            active=False,
            fraction=None,
            sample_n=n,
            reason="historique sans wins et losses — Kelly inactif",
        )
    wr = len(wins) / n
    avg_w = sum(wins) / len(wins)
    avg_l = sum(losses) / len(losses)
    b = avg_w / avg_l if avg_l > 0 else 0.0
    raw = wr - (1.0 - wr) / b if b > 0 else 0.0
    frac = max(0.0, min(0.5, raw * kelly_fraction))
    reasons: list[str] = []
    if vol_ratio_vs_baseline is not None and vol_ratio_vs_baseline >= 2.0:
        frac *= 0.5
        reasons.append("vol ×2 vs baseline → fraction réduite")
    if max_position_corr is not None and max_position_corr > 0.5:
        frac *= 0.5
        reasons.append("corrélation positions > 0.5 → fraction réduite")
    reasons.append("mode évaluation only — ne change pas la taille")
    return KellyEval(
        enabled=True,
        active=False,  # never activates sizing from this module alone
        fraction=frac,
        sample_n=n,
        reason="; ".join(reasons),
        suggested_size_mult=None,
    )
