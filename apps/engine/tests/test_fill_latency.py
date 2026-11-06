"""Realistic paper fills: latency meta + slip in price (no double-count in PnL)."""

from __future__ import annotations

from app.config import BotParams
from app.fill_model import apply_adverse, quote_fill
from app.wallet import PaperWallet


def test_quote_fill_records_decision_and_latency():
    q = quote_fill("buy", 100.0, slip_bps=10.0, latency_ms=250, impact_bps=0.0)
    assert q.decision_price == 100.0
    assert q.latency_ms == 250
    assert q.slip_bps == 10.0
    assert abs(q.fill_price - 100.1) < 1e-9


def test_sell_adverse_slippage():
    assert abs(apply_adverse("sell", 100.0, slip_bps=10.0) - 99.9) < 1e-9


def test_wallet_sell_pnl_does_not_reapply_slip():
    """Fill already embeds slip; realized uses proceeds − cost_basis only."""
    w = PaperWallet(initial_balance=10_000)
    w.buy(
        "BTC-USD",
        qty=1.0,
        price=100.0,
        fee_bps=0.0,
        decision_price=99.9,
        latency_ms=200,
        slip_bps=10.0,
    )
    # Mid 110, slip 10 bps → fill 109.89
    q = quote_fill("sell", 110.0, slip_bps=10.0, latency_ms=200)
    t = w.sell(
        "BTC-USD",
        qty=1.0,
        price=q.fill_price,
        fee_bps=0.0,
        decision_price=q.decision_price,
        latency_ms=q.latency_ms,
        slip_bps=q.slip_bps,
    )
    assert t.latency_ms == 200
    assert t.decision_price == 110.0
    # cost 100, proceeds 109.89 → 9.89 (no second slip subtraction)
    assert abs(t.realized_pnl - 9.89) < 1e-9


def test_bot_params_latency_defaults():
    p = BotParams()
    assert p.latency_ms == 250
    assert p.latency_wall_clock is True  # twin re-quotes after latency
    assert p.order_impact_enabled is False
    assert p.execution_twin_enabled is True
