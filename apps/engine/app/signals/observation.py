"""Timestamped signal observations — anti look-ahead (as_of ≤ ingested_at)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

ConfidenceTier = Literal["fact", "indicator", "hypothesis"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class SignalObservation:
    kind: str
    symbol: str
    as_of: datetime
    ingested_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    confidence_tier: ConfidenceTier = "hypothesis"
    source: str = "stub"
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        validate_observation_times(self.as_of, self.ingested_at)
        if self.confidence_tier not in ("fact", "indicator", "hypothesis"):
            raise ValueError(f"invalid confidence_tier: {self.confidence_tier}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "symbol": self.symbol,
            "as_of": self.as_of.isoformat(),
            "ingested_at": self.ingested_at.isoformat(),
            "payload": self.payload,
            "confidence_tier": self.confidence_tier,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> SignalObservation:
        as_of = _parse_ts(row.get("as_of"))
        ingested = _parse_ts(row.get("ingested_at"))
        if as_of is None or ingested is None:
            raise ValueError("as_of and ingested_at required")
        tier = str(row.get("confidence_tier") or "hypothesis")
        if tier not in ("fact", "indicator", "hypothesis"):
            raise ValueError(f"invalid confidence_tier: {tier}")
        return cls(
            id=str(row.get("id") or uuid4()),
            kind=str(row.get("kind") or ""),
            symbol=str(row.get("symbol") or ""),
            as_of=as_of,
            ingested_at=ingested,
            payload=dict(row.get("payload") or {}),
            confidence_tier=tier,  # type: ignore[arg-type]
            source=str(row.get("source") or "stub"),
        )


def validate_observation_times(as_of: datetime, ingested_at: datetime) -> None:
    a = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    i = ingested_at if ingested_at.tzinfo else ingested_at.replace(tzinfo=timezone.utc)
    if a > i:
        raise ValueError("as_of must be <= ingested_at (look-ahead guard)")


def make_observation(
    *,
    kind: str,
    symbol: str,
    as_of: datetime,
    payload: dict[str, Any] | None = None,
    confidence_tier: ConfidenceTier = "hypothesis",
    source: str = "stub",
    ingested_at: datetime | None = None,
) -> SignalObservation:
    return SignalObservation(
        kind=kind,
        symbol=symbol,
        as_of=as_of,
        ingested_at=ingested_at or _utc_now(),
        payload=payload or {},
        confidence_tier=confidence_tier,
        source=source,
    )
