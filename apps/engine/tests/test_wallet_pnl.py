"""Wallet / trade display accounting: win/loss, partial, fees, incomplete."""

from __future__ import annotations

from datetime import datetime, timezone

from app.risk import net_pnl_if_closed
from app.wallet import PaperWallet, Trade, annotate_trade_for_display


def test_buy_shows_invested_not_pnl():
    w = PaperWallet(initial_balance=10_000)
    t = w.buy("BTC-USD", qty=1.0, price=100.0, fee_bps=10.0)
    assert t.invested_quote == 100.0 + 0.1
    ann = annotate_trade_for_display(t)
    assert ann["display_kind"] == "invested"
    assert ann["display_amount"] == t.invested_quote
    assert ann["badge_label"] == "Investi"
    assert ann["tone"] == "neutral"


def test_sell_win_net_includes_entry_and_exit_fees():
    w = PaperWallet(initial_balance=10_000)
    w.buy("BTC-USD", qty=1.0, price=100.0, fee_bps=10.0)
    # Exit at 110 with fee — slip already in fill price.
    t = w.sell("BTC-USD", qty=1.0, price=110.0, fee_bps=10.0)
    # cost_basis = 100.1; proceeds = 110 - 0.11 = 109.89; net = 9.79
    assert t.meta_complete is True
    assert abs(t.realized_pnl - 9.79) < 1e-9
    ann = annotate_trade_for_display(t)
    assert ann["display_kind"] == "realized"
    assert ann["badge"] == "winner"
    assert ann["tone"] == "gain"
    assert ann["display_amount"] == t.realized_pnl
    assert ann["reference_price"] == 100.0


def test_sell_loss_badge():
    w = PaperWallet(initial_balance=10_000)
    w.buy("ETH-USD", qty=2.0, price=50.0, fee_bps=10.0)
    t = w.sell("ETH-USD", qty=2.0, price=40.0, fee_bps=10.0)
    assert t.realized_pnl < 0
    ann = annotate_trade_for_display(t)
    assert ann["badge"] == "loser"
    assert ann["tone"] == "loss"


def test_partial_sell_proportional_cost_basis():
    w = PaperWallet(initial_balance=10_000)
    w.buy("SOL-USD", qty=10.0, price=10.0, fee_bps=0.0)
    t1 = w.sell("SOL-USD", qty=4.0, price=12.0, fee_bps=0.0)
    assert abs(t1.realized_pnl - 8.0) < 1e-9
    pos = w.get_position("SOL-USD")
    assert pos is not None
    assert abs(pos.qty - 6.0) < 1e-9
    assert abs(pos.cost_basis - 60.0) < 1e-9
    t2 = w.sell("SOL-USD", qty=6.0, price=9.0, fee_bps=0.0)
    assert abs(t2.realized_pnl - (-6.0)) < 1e-9


def test_incomplete_legacy_trade_not_invented():
    legacy = Trade(
        id="old",
        symbol="BTC-USD",
        side="sell",
        qty=1.0,
        price=100.0,
        fee=0.1,
        ts=datetime.now(timezone.utc),
        realized_pnl=5.0,
        meta_complete=False,
        avg_entry_ref=None,
        cost_basis_closed=None,
    )
    ann = annotate_trade_for_display(legacy)
    assert ann["display_kind"] == "incomplete"
    assert ann["display_amount"] is None
    assert "incomplètes" in ann["amount_label"]
    assert ann["tone"] == "muted"


def test_net_pnl_if_closed_matches_wallet_cost_basis():
    w = PaperWallet(initial_balance=10_000)
    w.buy("BTC-USD", qty=1.0, price=100.0, fee_bps=10.0)
    pos = w.get_position("BTC-USD")
    assert pos is not None
    net = net_pnl_if_closed(
        pos.avg_entry,
        pos.qty,
        110.0,
        fee_bps=10.0,
        slippage_bps=0.0,
        cost_basis=pos.cost_basis,
    )
    # Hypothetical close at 110 no slip: proceeds 110 - 0.11, basis 100.1 → 9.79
    assert abs(float(net["net_pnl"]) - 9.79) < 1e-9
