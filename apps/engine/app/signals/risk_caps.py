"""Hard risk caps — non-negotiable, out of reach of signal AI / Kelly sizing."""

from __future__ import annotations

# Stop dur: 2% capital / trade. Never raise this from signal modules.
HARD_RISK_PER_TRADE_PCT = 0.02


def clamp_risk_per_trade_pct(value: float) -> float:
    """Clamp requested risk to (0, HARD_RISK_PER_TRADE_PCT]."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return HARD_RISK_PER_TRADE_PCT
    if v <= 0:
        return HARD_RISK_PER_TRADE_PCT
    return min(v, HARD_RISK_PER_TRADE_PCT)
