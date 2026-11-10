"""Copilot decision feed for the dashboard."""

from __future__ import annotations

from app.config import BotParams
from app.llm.copilot import CopilotDecision
from app.runtime import TradingRuntime


def test_record_copilot_appears_in_feed() -> None:
    rt = TradingRuntime()
    rt.params = BotParams(copilot_enabled=False)
    decision = CopilotDecision(
        action="veto",
        size_mult=1.0,
        reason_fr="Edge trop faible vs friction",
        soft_fail=False,
        provider="groq",
        model="llama",
        latency_ms=12,
    )
    rt._record_copilot(
        symbol="BTC-USD",
        kind="entry",
        side="buy",
        price=50_000.0,
        decision=decision,
        confidence=0.8,
    )
    feed = rt.copilot_feed()
    assert len(feed) == 1
    assert feed[0]["action"] == "veto"
    assert feed[0]["reason_fr"] == "Edge trop faible vs friction"
    assert feed[0]["provider"] == "groq"
    assert any(e.action == "copilot" for e in rt.journal.entries)
    view = rt.signal_view()
    assert view["copilot_decisions"][0]["action"] == "veto"
