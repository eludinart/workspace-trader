"""Point 14 — confluence score across independent families (disabled by default)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

FAMILY_KEYS = ("structure", "trend", "momentum", "volume", "liquidity")


@dataclass(frozen=True)
class ConfluenceResult:
    enabled: bool
    score: float | None
    family_scores: dict[str, float]
    active_families: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "score": self.score,
            "family_scores": self.family_scores,
            "active_families": self.active_families,
            "reason": self.reason,
        }


def score_confluence(
    features: Mapping[str, float] | None,
    *,
    enabled: bool,
    weights: Mapping[str, float] | None = None,
) -> ConfluenceResult:
    """Minimal usable confluence: map feature families → [-1, 1] then average.

    Does not invent independence — callers must supply distinct family scores.
    Percentages like « 55% RSI+MACD » are hypotheses, not constants shown as fact.
    """
    if not enabled:
        return ConfluenceResult(
            enabled=False,
            score=None,
            family_scores={},
            active_families=0,
            reason="confluence_enabled=false",
        )
    feats = dict(features or {})
    w = {k: float((weights or {}).get(k, 1.0)) for k in FAMILY_KEYS}
    family_scores: dict[str, float] = {}
    for key in FAMILY_KEYS:
        raw = feats.get(f"confluence_{key}")
        if raw is None:
            # Soft fallbacks from classic features (still one score per family).
            if key == "trend":
                raw = feats.get("macd_hist")
            elif key == "momentum":
                raw = feats.get("rsi")
                if raw is not None:
                    raw = (float(raw) - 50.0) / 50.0
            elif key == "volume":
                raw = feats.get("volume_ratio")
                if raw is not None:
                    raw = float(raw) - 1.0
            elif key == "structure":
                raw = feats.get("return_1")
            elif key == "liquidity":
                raw = feats.get("spread_bps")
                if raw is not None:
                    raw = -abs(float(raw)) / 100.0
        if raw is None:
            continue
        family_scores[key] = max(-1.0, min(1.0, float(raw)))

    if not family_scores:
        return ConfluenceResult(
            enabled=True,
            score=None,
            family_scores={},
            active_families=0,
            reason="no family scores available",
        )
    num = sum(family_scores[k] * w[k] for k in family_scores)
    den = sum(w[k] for k in family_scores) or 1.0
    return ConfluenceResult(
        enabled=True,
        score=float(num / den),
        family_scores=family_scores,
        active_families=len(family_scores),
        reason="ok",
    )
