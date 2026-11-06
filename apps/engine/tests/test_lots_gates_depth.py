"""Lots 1/7/8: entry gates shadow, Kelly eval-only, book depth, hard 2% risk."""

from __future__ import annotations

import pandas as pd

from app.fill_model import simulate_order_book, walk_book_fill
from app.signals.gates import evaluate_entry_gates
from app.signals.kelly import evaluate_kelly
from app.signals.mtf import tf_changes_from_candles
from app.signals.risk_caps import HARD_RISK_PER_TRADE_PCT, clamp_risk_per_trade_pct


def test_hard_risk_cap_clamps():
    assert HARD_RISK_PER_TRADE_PCT == 0.02
    assert clamp_risk_per_trade_pct(0.05) == 0.02
    assert clamp_risk_per_trade_pct(0.012) == 0.012


def test_kelly_stays_eval_only_without_oos_and_allow_sizing():
    pnls = [1.0] * 120 + [-0.5] * 100
    k = evaluate_kelly(pnls, enabled=True, min_sample=200, allow_sizing=False)
    assert k.active is False
    assert k.to_dict()["applies_to_sizing"] is False
    assert "éval" in k.reason.lower() or "sizing" in k.reason.lower() or "OOS" in k.reason


def test_entry_gate_shadow_does_not_block_without_oos():
    # Small sample → OOS refuse → shadow when flags on
    features = {
        "rsi": 40.0,
        "macd_hist": -0.1,
        "volume_ratio": 0.8,
        "return_1": -0.01,
    }
    candles = pd.DataFrame(
        {"close": [100 + i * 0.1 for i in range(50)], "high": 101, "low": 99, "volume": 1.0}
    )
    gate = evaluate_entry_gates(
        confluence_enabled=True,
        mtf_enabled=True,
        features=features,
        intended_side="buy",
        candles=candles,
        trade_pnls=[1.0] * 5,
    )
    assert gate.enforce is False
    assert gate.shadow is True
    assert gate.blocked is False
    assert "shadow" in gate.reason


def test_tf_changes_from_hourly_candles():
    closes = [100.0] * 30
    closes[-1] = 110.0
    df = pd.DataFrame({"close": closes})
    ch = tf_changes_from_candles(df, bar_seconds=3600)
    assert ch["15m"] is not None or ch["4h"] is not None or ch["1d"] is not None


def test_walk_book_fill_adverse_for_buy():
    book = simulate_order_book(100.0, depth_levels=3)
    assert book.label.startswith("simulated_book")
    q = walk_book_fill("buy", 100.0, notional=50.0, latency_ms=200, depth_levels=5)
    assert q.fill_price >= 100.0
    assert q.latency_ms == 200
