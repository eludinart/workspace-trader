"""Lot 0 + stubs 14–21: flags off, observations, OOS refuse activation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import BotParams
from app.signals.confluence import score_confluence
from app.signals.flags import SignalFeatureFlags, default_flags
from app.signals.kelly import evaluate_kelly
from app.signals.mtf import evaluate_mtf_gate
from app.signals.observation import make_observation, validate_observation_times
from app.signals.onchain import stub_onchain
from app.signals.oos import evaluate_oos
from app.signals.passive_wake import evaluate_passive_wake
from app.signals.post_trade import run_post_trade_review
from app.signals.super_opp import detect_super_opportunity


def test_all_signal_flags_false_by_default():
    flags = default_flags()
    assert flags.model_dump() == {
        "confluence_enabled": False,
        "mtf_enabled": False,
        "kelly_enabled": False,
        "onchain_enabled": False,
        "super_opp_enabled": False,
        "passive_wake_enabled": False,
        "post_trade_ai_enabled": False,
        "web_push_enabled": False,
    }
    params = BotParams()
    assert params.confluence_enabled is False
    assert params.mtf_enabled is False
    assert params.kelly_enabled is False
    assert params.onchain_enabled is False
    assert params.super_opp_enabled is False
    assert params.passive_wake_enabled is False
    assert params.post_trade_ai_enabled is False
    assert params.web_push_enabled is False


def test_observation_rejects_as_of_after_ingested():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="as_of"):
        validate_observation_times(now + timedelta(minutes=1), now)
    with pytest.raises(ValueError, match="as_of"):
        make_observation(
            kind="test",
            symbol="BTC-USD",
            as_of=now + timedelta(seconds=5),
            ingested_at=now,
        )


def test_observation_ok_when_as_of_le_ingested():
    now = datetime.now(timezone.utc)
    obs = make_observation(
        kind="test",
        symbol="BTC-USD",
        as_of=now - timedelta(seconds=1),
        ingested_at=now,
        confidence_tier="indicator",
    )
    assert obs.confidence_tier == "indicator"
    assert obs.as_of <= obs.ingested_at


def test_oos_never_allows_activation():
    pnls = [1.0] * 100 + [-0.5] * 50
    report = evaluate_oos(pnls, min_holdout=10)
    assert report.activation_allowed is False
    assert "activation" in report.activation_block_reason.lower() or "validation" in report.activation_block_reason.lower()


def test_stubs_disabled_expose_reason():
    assert score_confluence({}, enabled=False).reason == "confluence_enabled=false"
    mtf = evaluate_mtf_gate(enabled=False, intended_side="buy")
    assert mtf.allowed is True
    assert "mtf_enabled=false" in mtf.reason
    kelly = evaluate_kelly([1.0] * 10, enabled=True, min_sample=200)
    assert kelly.active is False
    assert "échantillon" in kelly.reason
    onchain = stub_onchain("whales", "BTC-USD", enabled=False)
    assert onchain.observation is None
    opp = detect_super_opportunity(enabled=False)
    assert opp.detected is False
    wake = evaluate_passive_wake(enabled=False, move_pct=0.05)
    assert wake.should_wake_ai is False
    assert run_post_trade_review(enabled=False, trade={"id": "x", "side": "sell"}) is None


def test_mtf_enabled_incomplete_blocks():
    r = evaluate_mtf_gate(enabled=True, intended_side="buy")
    assert r.allowed is False
    assert "incomplètes" in r.reason


def test_signal_feature_flags_model():
    f = SignalFeatureFlags(confluence_enabled=True)
    assert f.confluence_enabled is True
    assert f.kelly_enabled is False
