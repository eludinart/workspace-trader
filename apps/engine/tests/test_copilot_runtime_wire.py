"""Copilot config + soft-fail leave the XGBoost path unchanged."""

from __future__ import annotations

from app.config import BotParams
from app.llm.copilot import CopilotService
from app.runtime import TradingRuntime
from app.strategy.xgboost_strategy import SignalResult


def test_bot_params_copilot_defaults() -> None:
    p = BotParams()
    assert p.copilot_enabled is False
    assert p.copilot_review_interval_sec == 180


def test_system_status_includes_copilot_feature() -> None:
    rt = TradingRuntime()
    board = rt.system_status()
    ids = {f["id"] for f in board["features"]}
    assert "copilot" in ids
    row = next(f for f in board["features"] if f["id"] == "copilot")
    assert row["state"] in {"ok", "off", "warn", "error"}


def test_copilot_off_skips_review(monkeypatch) -> None:
    rt = TradingRuntime()
    rt.params = BotParams(copilot_enabled=False)
    called = {"n": 0}

    def boom(_ctx):  # noqa: ANN001
        called["n"] += 1
        raise AssertionError("should not call LLM")

    monkeypatch.setattr(rt.copilot, "review_entry", boom)
    signal = SignalResult(
        action="buy",
        confidence=0.9,
        probs={"buy": 0.9, "hold": 0.05, "sell": 0.05},
        features={"rsi": 40.0, "macd_hist": 0.1},
        model_ready=True,
        detail="buy_th=0.62 sell_th=0.50",
    )
    decision = rt._apply_copilot_entry(
        symbol="BTC-USD",
        signal=signal,
        side="buy",
        price=100.0,
        proposed_notional=50.0,
    )
    assert decision is None
    assert called["n"] == 0


def test_status_state_off_by_default() -> None:
    state, detail = CopilotService().status_state(False)
    assert state == "off"
    assert "OFF" in detail
