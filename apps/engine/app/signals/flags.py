"""Feature flags for advanced signal modules — all OFF by default."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SignalFeatureFlags(BaseModel):
    """Runtime toggles. Never enable in production without explicit OK."""

    confluence_enabled: bool = False
    mtf_enabled: bool = False
    kelly_enabled: bool = False
    onchain_enabled: bool = False
    super_opp_enabled: bool = False
    passive_wake_enabled: bool = False
    post_trade_ai_enabled: bool = False
    # Web Push is UI/env gated; keep a server flag for docs/status.
    web_push_enabled: bool = False

    def as_dict(self) -> dict[str, bool]:
        return self.model_dump()


def default_flags() -> SignalFeatureFlags:
    return SignalFeatureFlags()


def flags_from_mapping(raw: Any) -> SignalFeatureFlags:
    if not isinstance(raw, dict):
        return default_flags()
    return SignalFeatureFlags.model_validate(
        {k: raw.get(k, False) for k in SignalFeatureFlags.model_fields}
    )


# Env-style names documented in .env.example (Coolify) — defaults False.
FLAG_ENV_KEYS: dict[str, str] = {
    "confluence_enabled": "SIGNAL_CONFLUENCE_ENABLED",
    "mtf_enabled": "SIGNAL_MTF_ENABLED",
    "kelly_enabled": "SIGNAL_KELLY_ENABLED",
    "onchain_enabled": "SIGNAL_ONCHAIN_ENABLED",
    "super_opp_enabled": "SIGNAL_SUPER_OPP_ENABLED",
    "passive_wake_enabled": "SIGNAL_PASSIVE_WAKE_ENABLED",
    "post_trade_ai_enabled": "SIGNAL_POST_TRADE_AI_ENABLED",
    "web_push_enabled": "WEB_PUSH_ENABLED",
}


def flags_from_env(getenv) -> SignalFeatureFlags:
    """Build flags from an env getter (e.g. os.getenv). Truthy: 1/true/yes/on."""

    def _truthy(key: str) -> bool:
        raw = getenv(FLAG_ENV_KEYS[key], "") or ""
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    return SignalFeatureFlags(**{k: _truthy(k) for k in FLAG_ENV_KEYS})


class HardRiskCaps(BaseModel):
    """Non-negotiable risk caps — not modifiable by signal AI."""

    max_risk_per_trade_pct: float = Field(default=0.02, gt=0, le=0.02)
    description: str = "Stop dur 2% capital / trade — hors portée IA"
