"""Point 15 — multi-timeframe Daily / 4h / 15m gate (disabled by default)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

import pandas as pd

Bias = Literal["bull", "bear", "neutral", "unknown"]


@dataclass(frozen=True)
class TfReading:
    timeframe: str
    bias: Bias
    as_of: datetime | None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "bias": self.bias,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class MtfGateResult:
    enabled: bool
    allowed: bool
    reason: str
    readings: list[TfReading]

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "allowed": self.allowed,
            "reason": self.reason,
            "readings": [r.to_dict() for r in self.readings],
        }


def _bias_from_change(change: float | None) -> Bias:
    if change is None:
        return "unknown"
    if change > 0.002:
        return "bull"
    if change < -0.002:
        return "bear"
    return "neutral"


def _pct_change_over(closes: pd.Series, bars: int) -> float | None:
    if closes is None or len(closes) <= bars:
        return None
    a = float(closes.iloc[-1])
    b = float(closes.iloc[-(bars + 1)])
    if b == 0:
        return None
    return (a - b) / b


def tf_changes_from_candles(
    candles: Any,
    *,
    bar_seconds: int | None = None,
) -> dict[str, float | None]:
    """Derive approx 15m / 4h / 1d returns from a single candle frame.

    When bar size is unknown, assume hourly (3600s) — Coinbase default for engine.
    Returns None for a TF when not enough bars exist (gate treats as incomplete).
    """
    if candles is None:
        return {"15m": None, "4h": None, "1d": None}
    if isinstance(candles, pd.DataFrame):
        df = candles
    else:
        try:
            df = pd.DataFrame(candles)
        except Exception:  # noqa: BLE001
            return {"15m": None, "4h": None, "1d": None}
    if "close" not in df.columns or len(df) < 2:
        return {"15m": None, "4h": None, "1d": None}
    closes = df["close"].astype(float)
    sec = int(bar_seconds or 3600)
    # bars needed for each horizon
    need = {
        "15m": max(1, int(round(900 / sec))),
        "4h": max(1, int(round(14_400 / sec))),
        "1d": max(1, int(round(86_400 / sec))),
    }
    return {k: _pct_change_over(closes, n) for k, n in need.items()}


def evaluate_mtf_gate(
    *,
    enabled: bool,
    intended_side: str,
    daily_change: float | None = None,
    h4_change: float | None = None,
    m15_change: float | None = None,
    as_of: datetime | None = None,
) -> MtfGateResult:
    """Block entries against higher-TF bias when enabled and data complete."""
    now = as_of or datetime.now(timezone.utc)
    readings = [
        TfReading("1d", _bias_from_change(daily_change), now if daily_change is not None else None),
        TfReading("4h", _bias_from_change(h4_change), now if h4_change is not None else None),
        TfReading("15m", _bias_from_change(m15_change), now if m15_change is not None else None),
    ]
    if not enabled:
        return MtfGateResult(
            enabled=False,
            allowed=True,
            reason="mtf_enabled=false",
            readings=readings,
        )
    if any(r.bias == "unknown" for r in readings):
        return MtfGateResult(
            enabled=True,
            allowed=False,
            reason="données MTF incomplètes — pas d'alignement forcé",
            readings=readings,
        )
    side = intended_side.lower()
    daily, h4 = readings[0].bias, readings[1].bias
    if side == "buy" and daily == "bear":
        return MtfGateResult(
            enabled=True,
            allowed=False,
            reason="blocage: tendance Daily bear vs achat",
            readings=readings,
        )
    if side == "sell" and daily == "bull" and h4 == "bull":
        return MtfGateResult(
            enabled=True,
            allowed=False,
            reason="blocage: Daily+4h bull vs vente discrétionnaire",
            readings=readings,
        )
    return MtfGateResult(enabled=True, allowed=True, reason="ok", readings=readings)
