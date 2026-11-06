"""Point 20 — passive deterministic watch; wake AI only on threshold (disabled)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class PassiveWatchResult:
    enabled: bool
    should_wake_ai: bool
    reason: str
    metric: str
    value: float | None
    threshold: float
    as_of: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "should_wake_ai": self.should_wake_ai,
            "reason": self.reason,
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
            "as_of": self.as_of,
            "note": "zéro IA au repos ≠ zéro réseau; réveil idempotent à journaliser",
        }


def evaluate_passive_wake(
    *,
    enabled: bool,
    move_pct: float | None,
    threshold_pct: float = 0.015,
    as_of: datetime | None = None,
) -> PassiveWatchResult:
    ts = (as_of or datetime.now(timezone.utc)).isoformat()
    if not enabled:
        return PassiveWatchResult(
            enabled=False,
            should_wake_ai=False,
            reason="passive_wake_enabled=false",
            metric="abs_move_pct",
            value=move_pct,
            threshold=threshold_pct,
            as_of=ts,
        )
    if move_pct is None:
        return PassiveWatchResult(
            enabled=True,
            should_wake_ai=False,
            reason="pas de métrique — surveillance idle",
            metric="abs_move_pct",
            value=None,
            threshold=threshold_pct,
            as_of=ts,
        )
    wake = abs(float(move_pct)) >= float(threshold_pct)
    return PassiveWatchResult(
        enabled=True,
        should_wake_ai=wake,
        reason="seuil atteint" if wake else "sous seuil — pas d'appel IA",
        metric="abs_move_pct",
        value=float(move_pct),
        threshold=threshold_pct,
        as_of=ts,
    )
