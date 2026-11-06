"""Entry gates: confluence + MTF with shadow vs enforce modes.

Enforce only when OOS harness says activation_allowed.
Otherwise flags may be ON in shadow (log / journal) without blocking trades.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.signals.confluence import ConfluenceResult, score_confluence
from app.signals.mtf import MtfGateResult, evaluate_mtf_gate, tf_changes_from_candles
from app.signals.oos import OosReport, evaluate_oos


@dataclass(frozen=True)
class EntryGateDecision:
    shadow: bool
    enforce: bool
    blocked: bool
    reason: str
    confluence: ConfluenceResult
    mtf: MtfGateResult
    oos: OosReport | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "shadow": self.shadow,
            "enforce": self.enforce,
            "blocked": self.blocked,
            "reason": self.reason,
            "confluence": self.confluence.to_dict(),
            "mtf": self.mtf.to_dict(),
            "oos": self.oos.to_dict() if self.oos else None,
        }


def evaluate_entry_gates(
    *,
    confluence_enabled: bool,
    mtf_enabled: bool,
    features: Mapping[str, float] | None,
    intended_side: str,
    candles: Any | None = None,
    trade_pnls: Sequence[float] | None = None,
    min_confluence_families: int = 2,
    min_confluence_score: float = 0.05,
) -> EntryGateDecision:
    """Compute confluence + MTF. Block only if enforce (OOS OK) and a gate fails."""
    oos = evaluate_oos(list(trade_pnls or []))
    enforce = bool(oos.activation_allowed)
    shadow = (confluence_enabled or mtf_enabled) and not enforce

    conf = score_confluence(features, enabled=confluence_enabled)
    changes = tf_changes_from_candles(candles) if candles is not None else {}
    mtf = evaluate_mtf_gate(
        enabled=mtf_enabled,
        intended_side=intended_side,
        daily_change=changes.get("1d"),
        h4_change=changes.get("4h"),
        m15_change=changes.get("15m"),
    )

    block_reasons: list[str] = []
    if confluence_enabled and conf.score is not None:
        if conf.active_families < min_confluence_families:
            block_reasons.append(
                f"confluence familles={conf.active_families}<{min_confluence_families}"
            )
        elif conf.score < min_confluence_score and intended_side.lower() == "buy":
            block_reasons.append(f"confluence score={conf.score:.3f}<{min_confluence_score}")
    if mtf_enabled and not mtf.allowed:
        block_reasons.append(mtf.reason)

    would_block = bool(block_reasons) and intended_side.lower() in {"buy", "sell"}
    if enforce and would_block:
        return EntryGateDecision(
            shadow=False,
            enforce=True,
            blocked=True,
            reason="; ".join(block_reasons),
            confluence=conf,
            mtf=mtf,
            oos=oos,
        )
    if shadow and would_block:
        return EntryGateDecision(
            shadow=True,
            enforce=False,
            blocked=False,
            reason=f"shadow (OOS refuse) — {'; '.join(block_reasons)}",
            confluence=conf,
            mtf=mtf,
            oos=oos,
        )
    mode = "enforce" if enforce else ("shadow" if shadow else "off")
    return EntryGateDecision(
        shadow=shadow,
        enforce=enforce,
        blocked=False,
        reason=f"ok ({mode})",
        confluence=conf,
        mtf=mtf,
        oos=oos,
    )
