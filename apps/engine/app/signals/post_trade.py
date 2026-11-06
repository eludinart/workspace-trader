"""Point 21 — async post-trade AI commentary (never mutates PnL/positions; soft-fail)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class PostTradeReview:
    id: str
    trade_id: str
    symbol: str
    ts: datetime
    comment: str
    source: str = "post_trade_ai"
    ok: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "ts": self.ts.isoformat(),
            "comment": self.comment,
            "source": self.source,
            "ok": self.ok,
            "error": self.error,
            "mutates_pnl": False,
            "mutates_positions": False,
            "usable_in_backtest_pretrade": False,
        }


def build_post_trade_comment(
    *,
    trade: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> str:
    """Deterministic placeholder comment (no LLM required). Soft, journal-only."""
    side = str(trade.get("side") or "")
    sym = str(trade.get("symbol") or "")
    pnl = trade.get("realized_pnl")
    note = str(trade.get("note") or "")
    ctx = context or {}
    bits = [
        f"Revue post-trade {side} {sym}.",
        f"Note exécution: {note or 'n/a'}.",
        f"P&L réalisé (figé, non modifié): {pnl}.",
    ]
    if ctx.get("signal_action"):
        bits.append(f"Signal associé: {ctx.get('signal_action')}.")
    bits.append("Leçon: vérifier frais/slippage vs thèse — commentaire séparé du journal.")
    return " ".join(bits)


def run_post_trade_review(
    *,
    enabled: bool,
    trade: dict[str, Any],
    context: dict[str, Any] | None = None,
    append_journal: Callable[[dict[str, Any]], None] | None = None,
) -> PostTradeReview | None:
    """Generate review after a close. Fail soft; never touches wallet."""
    if not enabled:
        return None
    trade_id = str(trade.get("id") or "")
    symbol = str(trade.get("symbol") or "")
    try:
        comment = build_post_trade_comment(trade=trade, context=context)
        review = PostTradeReview(
            id=str(uuid4()),
            trade_id=trade_id,
            symbol=symbol,
            ts=datetime.now(timezone.utc),
            comment=comment,
            ok=True,
        )
        if append_journal is not None:
            append_journal(
                {
                    "type": "post_trade_review",
                    "review": review.to_dict(),
                }
            )
        return review
    except Exception as exc:  # noqa: BLE001
        logger.warning("post_trade_review soft-fail: %s", exc)
        return PostTradeReview(
            id=str(uuid4()),
            trade_id=trade_id,
            symbol=symbol,
            ts=datetime.now(timezone.utc),
            comment="",
            ok=False,
            error=str(exc),
        )
