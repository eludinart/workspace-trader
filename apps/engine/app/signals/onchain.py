"""Point 17 — on-chain interfaces (whales / funding / liquidations). Labeled stubs only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from app.signals.observation import ConfidenceTier, SignalObservation, make_observation

OnchainKind = Literal["whales", "funding", "liquidations"]

# Documented possible sources — not wired; no fabricated live numbers.
POSSIBLE_SOURCES: dict[str, list[str]] = {
    "whales": ["Whale Alert API", "Glassnode entity flows", "exchange cold-wallet trackers"],
    "funding": ["Coinbase derivatives funding", "Binance funding (read-only)", "Coinglass"],
    "liquidations": ["Coinglass liquidation heatmaps", "exchange public liq feeds"],
}

# Chosen defaults for future wiring (documented; still stubs until API keys in Coolify).
PREFERRED_SOURCES: dict[str, str] = {
    "whales": "Whale Alert API",
    "funding": "Coinglass",
    "liquidations": "Coinglass liquidation heatmaps",
}


@dataclass(frozen=True)
class OnchainStub:
    kind: OnchainKind
    enabled: bool
    observation: SignalObservation | None
    label: str
    possible_sources: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "enabled": self.enabled,
            "label": self.label,
            "possible_sources": self.possible_sources,
            "preferred_source": PREFERRED_SOURCES.get(self.kind),
            "observation": self.observation.to_dict() if self.observation else None,
        }


def stub_onchain(
    kind: OnchainKind,
    symbol: str,
    *,
    enabled: bool,
    as_of: datetime | None = None,
) -> OnchainStub:
    """Return a labeled stub. Never invents numeric facts without a source tag."""
    sources = list(POSSIBLE_SOURCES.get(kind, []))
    if not enabled:
        return OnchainStub(
            kind=kind,
            enabled=False,
            observation=None,
            label="onchain_enabled=false",
            possible_sources=sources,
        )
    tier: ConfidenceTier = "hypothesis"
    obs = make_observation(
        kind=f"onchain_{kind}",
        symbol=symbol,
        as_of=as_of or datetime.now(timezone.utc),
        payload={
            "status": "stub",
            "message": "Pas de donnée live — stub étiqueté hypothesis",
            "value": None,
        },
        confidence_tier=tier,
        source="stub:unwired",
    )
    return OnchainStub(
        kind=kind,
        enabled=True,
        observation=obs,
        label="stub — aucune donnée factice sans label",
        possible_sources=sources,
    )
