"""Coinbase Advanced fee tiers for the execution twin (shadow taker fills)."""

from __future__ import annotations

from typing import Any, Literal

FeeTier = Literal["us_intro", "eu_intro", "custom"]

# Spot Advanced intro tiers (nov 2026 public comparisons). Confirm in-account.
FEE_TIER_PRESETS: dict[str, dict[str, float]] = {
    "us_intro": {"maker_fee_bps": 50.0, "taker_fee_bps": 90.0},
    "eu_intro": {"maker_fee_bps": 25.0, "taker_fee_bps": 50.0},
}


def normalize_fee_tier(value: Any) -> FeeTier:
    text = str(value or "us_intro").strip().lower()
    if text in ("us_intro", "eu_intro", "custom"):
        return text  # type: ignore[return-value]
    return "us_intro"


def resolve_fee_bps(
    tier: FeeTier,
    *,
    maker_fee_bps: float | None = None,
    taker_fee_bps: float | None = None,
) -> tuple[float, float]:
    """Return (maker_bps, taker_bps). Custom keeps provided values; presets overwrite."""
    if tier == "custom":
        maker = float(maker_fee_bps if maker_fee_bps is not None else 50.0)
        taker = float(taker_fee_bps if taker_fee_bps is not None else 90.0)
        return max(0.0, maker), max(0.0, taker)
    preset = FEE_TIER_PRESETS[tier]
    return float(preset["maker_fee_bps"]), float(preset["taker_fee_bps"])
