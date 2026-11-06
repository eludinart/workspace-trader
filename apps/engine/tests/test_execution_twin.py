"""Execution twin: real L2 walk, fee tiers, fidelity gate."""

from __future__ import annotations

from app.config import BotParams
from app.fees import resolve_fee_bps
from app.fidelity import FillSample, FidelityTracker
from app.fill_model import walk_real_book
from datetime import datetime, timezone


def test_us_intro_tier_defaults():
    p = BotParams()
    assert p.execution_twin_enabled is True
    assert p.fee_tier == "us_intro"
    assert p.taker_fee_bps == 90.0
    assert p.maker_fee_bps == 50.0
    assert p.fee_bps == 90.0  # wallet alias


def test_eu_intro_tier():
    p = BotParams(fee_tier="eu_intro")
    assert p.taker_fee_bps == 50.0
    assert p.maker_fee_bps == 25.0
    assert p.fee_bps == 50.0


def test_custom_tier_keeps_values():
    maker, taker = resolve_fee_bps("custom", maker_fee_bps=12.0, taker_fee_bps=34.0)
    assert maker == 12.0
    assert taker == 34.0


def test_walk_real_book_buy_vwap_above_best_ask():
    bids = [(99.0, 10.0), (98.0, 10.0)]
    asks = [(100.0, 0.5), (101.0, 0.5), (102.0, 5.0)]
    # Spend enough to walk into second ask.
    q = walk_real_book(
        "buy",
        bids=bids,
        asks=asks,
        notional=100.0,  # 0.5*100 + 0.5*101 = 100.5 capacity on first two
        decision_price=99.5,
        latency_ms=0,
        min_fill_notional=10.0,
    )
    assert not q.rejected
    assert q.source == "coinbase_l2"
    assert q.fill_price >= 100.0
    assert q.slip_bps > 0
    assert q.levels_consumed >= 1


def test_walk_real_book_reject_thin_book():
    q = walk_real_book(
        "buy",
        bids=[(99.0, 1.0)],
        asks=[(100.0, 0.01)],
        notional=500.0,
        decision_price=100.0,
        min_fill_notional=15.0,
    )
    assert q.rejected
    assert q.reject_reason == "insufficient_depth"
    assert q.fill_price == 0.0


def test_fidelity_gate_blocks_until_sample():
    tracker = FidelityTracker(min_fills=5, min_days=0.0)
    report = tracker.report(twin_enabled=True, fee_tier="us_intro", taker_fee_bps=90.0)
    assert report["go_live_ready"] is False
    assert any("fills_below_min" in r for r in report["block_reasons"])

    now = datetime.now(timezone.utc)
    for i in range(5):
        tracker.record(
            FillSample(
                ts=now,
                symbol="BTC-USD",
                side="buy",
                source="coinbase_l2",
                slip_bps=2.0,
                impact_bps=0.0,
                filled_notional=100.0,
                unfilled_notional=0.0,
                rejected=False,
                fee_bps=90.0,
            )
        )
    report2 = tracker.report(twin_enabled=True, fee_tier="us_intro", taker_fee_bps=90.0)
    assert report2["twin_fill_count"] == 5
    assert report2["go_live_ready"] is True
