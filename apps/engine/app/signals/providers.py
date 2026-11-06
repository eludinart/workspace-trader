"""Labeled external data providers for on-chain / derivatives.

No live fetches here — interfaces + documented sources only.
Any numeric payload without a source must stay ``hypothesis``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

ConfidenceTier = Literal["fact", "indicator", "hypothesis"]


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    kind: str
    sources: tuple[str, ...]
    confidence_default: ConfidenceTier
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "sources": list(self.sources),
            "confidence_default": self.confidence_default,
            "notes": self.notes,
            "wired": False,
        }


class OnchainProvider(Protocol):
    """Future live provider — must tag every value with confidence_tier + source."""

    id: str

    def fetch(self, symbol: str) -> dict[str, Any]:
        ...


DOCUMENTED_PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="whale_alert",
        kind="whales",
        sources=("Whale Alert API",),
        confidence_default="indicator",
        notes="Large transfers — indicator of flow, not price direction fact.",
    ),
    ProviderSpec(
        id="glassnode_flows",
        kind="whales",
        sources=("Glassnode entity flows",),
        confidence_default="indicator",
        notes="Requires paid API; not wired.",
    ),
    ProviderSpec(
        id="exchange_funding",
        kind="funding",
        sources=("Coinbase derivatives funding", "Binance funding read-only", "Coinglass"),
        confidence_default="fact",
        notes="Funding rate itself can be fact if taken from venue; interpretation = hypothesis.",
    ),
    ProviderSpec(
        id="liq_heatmaps",
        kind="liquidations",
        sources=("Coinglass liquidation heatmaps", "exchange public liq feeds"),
        confidence_default="indicator",
        notes="Aggregated heatmaps are indicators; venue prints may be facts.",
    ),
    ProviderSpec(
        id="basis_spot_perp",
        kind="super_opp",
        sources=("venue mark vs index", "public perp premium"),
        confidence_default="indicator",
        notes="Basis divergence feeds super-opportunity stub; sizing still ≤2% hard cap.",
    ),
)


def list_provider_specs() -> list[dict[str, Any]]:
    return [p.to_dict() for p in DOCUMENTED_PROVIDERS]
