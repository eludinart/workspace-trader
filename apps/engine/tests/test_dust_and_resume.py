"""Dust write-off + resume restores auto_trade + fresher last_tick."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.fill_model import FillQuote
from app.runtime import TradingRuntime


def test_dust_sell_bypasses_rejected_twin() -> None:
    rt = TradingRuntime()
    try:
        rt.lease = object()  # pretend we own fills
        rt._confirm_lease = lambda respect_release=False: True  # type: ignore[method-assign]
        rt.wallet.reset(1_000.0)
        # Tiny leftover ~$0.007 — below twin_min_fill_notional (15).
        rt.wallet.buy("BTC-USD", qty=1e-7, price=70_000.0, fee_bps=0.0, note="seed")
        rt.prices["BTC-USD"] = 71_000.0

        def _reject(*_a, **_k):
            raise AssertionError("twin must not be called for dust")

        rt._build_fill_quote = _reject  # type: ignore[method-assign]
        trade = rt._sell_qty("BTC-USD", 1e-7, 71_000.0, "exit:kill")
        assert trade is not None
        assert "dust" in (trade.note or "")
        assert rt.wallet.get_position("BTC-USD") is None
    finally:
        rt.market.close()


def test_resume_reenables_auto_trade() -> None:
    rt = TradingRuntime()
    try:
        rt.params.auto_trade = False
        rt.killed = True
        rt.kill_reason = "max_loss_-5.05%"
        rt._save_params = lambda: None  # type: ignore[method-assign]
        rt._persist_state = lambda: None  # type: ignore[method-assign]
        out = rt.resume_from_kill()
        assert rt.killed is False
        assert rt.kill_reason is None
        assert rt.params.auto_trade is True
        assert out["auto_trade"] is True
    finally:
        rt.market.close()


def test_effective_last_tick_prefers_newer_history() -> None:
    rt = TradingRuntime()
    try:
        stale = datetime.now(timezone.utc) - timedelta(hours=26)
        fresh = datetime.now(timezone.utc) - timedelta(seconds=8)
        rt.last_tick_at = stale
        rt.last_signal_symbol = "BTC-USD"
        rt.signal_history = [
            {
                "ts": fresh.isoformat(),
                "symbol": "ETH-USD",
                "decision": {"action": "hold"},
            }
        ]
        got_ts, got_sym = rt._effective_last_tick()
        assert got_sym == "ETH-USD"
        assert got_ts is not None
        assert abs((got_ts - fresh).total_seconds()) < 1

        board = rt.system_status()
        row = next(f for f in board["features"] if f["id"] == "last_tick")
        assert row["state"] == "ok"
        assert "ETH-USD" in row["detail"]
    finally:
        rt.market.close()


def test_flatten_clears_dust_position() -> None:
    rt = TradingRuntime()
    try:
        rt.lease = object()
        rt._confirm_lease = lambda respect_release=False: True  # type: ignore[method-assign]
        rt.wallet.reset(700.0)
        rt.wallet.buy("BTC-USD", qty=9.9e-8, price=74_000.0, fee_bps=0.0, note="seed")
        rt.prices["BTC-USD"] = 71_000.0
        rt._build_fill_quote = MagicMock(
            return_value=FillQuote(
                decision_ts=datetime.now(timezone.utc),
                decision_price=71_000.0,
                fill_price=0.0,
                latency_ms=0,
                slip_bps=0.0,
                impact_bps=0.0,
                rejected=True,
                reject_reason="insufficient_depth",
            )
        )
        closed = rt._flatten_all()
        assert closed
        assert rt.wallet.get_position("BTC-USD") is None
    finally:
        rt.market.close()
