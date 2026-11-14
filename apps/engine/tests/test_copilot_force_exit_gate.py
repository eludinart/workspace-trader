"""Hard gates for LLM force_exit — no fee churn."""

from __future__ import annotations

from app.risk import copilot_force_exit_allowed


def test_force_exit_blocked_while_fee_underwater_and_in_min_hold() -> None:
    ok, reason = copilot_force_exit_allowed(
        avg_entry=100.0,
        mark=99.5,  # -0.5% << 1.8% RT friction
        held_sec=120.0,
        min_hold_sec=300.0,
        stop_pct=0.022,
        fee_bps=90.0,
        slippage_bps=5.0,
    )
    assert ok is False
    assert reason == "min_hold"


def test_force_exit_blocked_fee_underwater_after_min_hold() -> None:
    ok, reason = copilot_force_exit_allowed(
        avg_entry=100.0,
        mark=99.5,
        held_sec=600.0,
        min_hold_sec=300.0,
        stop_pct=0.022,
        fee_bps=90.0,
        slippage_bps=5.0,
    )
    assert ok is False
    assert reason == "fee_underwater"


def test_force_exit_allowed_on_stop_hit_even_inside_min_hold() -> None:
    ok, reason = copilot_force_exit_allowed(
        avg_entry=100.0,
        mark=97.5,  # -2.5% > 2.2% stop
        held_sec=60.0,
        min_hold_sec=300.0,
        stop_pct=0.022,
        fee_bps=90.0,
        slippage_bps=5.0,
    )
    assert ok is True
    assert reason == "stop_hit"


def test_force_exit_allowed_when_net_green_after_min_hold() -> None:
    ok, reason = copilot_force_exit_allowed(
        avg_entry=100.0,
        mark=103.0,  # clears ~1.9% friction
        held_sec=600.0,
        min_hold_sec=300.0,
        stop_pct=0.022,
        fee_bps=90.0,
        slippage_bps=5.0,
    )
    assert ok is True
    assert reason == "net_green"
