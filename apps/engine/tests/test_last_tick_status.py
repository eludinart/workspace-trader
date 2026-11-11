"""API system status must show last tick from persisted signal_history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.runtime import TradingRuntime


def test_effective_last_tick_from_signal_history() -> None:
    rt = TradingRuntime()
    assert rt.last_tick_at is None
    ts = datetime.now(timezone.utc) - timedelta(seconds=12)
    rt.signal_history = [
        {
            "ts": ts.isoformat(),
            "symbol": "ETH-USD",
            "narrative": "ATTENTE ETH-USD",
            "decision": {"action": "hold"},
            "signal": {"action": "hold", "confidence": 0.4},
        }
    ]
    got_ts, got_sym = rt._effective_last_tick()
    assert got_sym == "ETH-USD"
    assert got_ts is not None
    assert abs((got_ts - ts).total_seconds()) < 1

    board = rt.system_status()
    row = next(f for f in board["features"] if f["id"] == "last_tick")
    assert row["state"] == "ok"
    assert "ETH-USD" in row["detail"]
