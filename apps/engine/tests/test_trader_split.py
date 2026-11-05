"""API process must not fill; trader persists exit levels and reloads params."""

from __future__ import annotations

import asyncio

import pytest

from app import db
from app.lease import LeaseManager, read_lease
from app.runtime import LeaseLost, TradingRuntime
from app.store import Store
from app.wallet import Position


def _reset_bot_lease() -> None:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE bot_lease
                SET owner_id=NULL, heartbeat_at=NULL, expires_at=NULL,
                    release_requested=%s, version=0
                WHERE id=%s
                """,
                (False, 1),
            )
        conn.commit()


def test_api_start_does_not_run_loops(store_db: None) -> None:
    runtime = TradingRuntime(store=Store())
    asyncio.run(runtime.start())
    assert runtime.running is False
    assert runtime._task is None
    runtime.market.close()


def test_api_refuses_fills_and_ticks(store_db: None) -> None:
    runtime = TradingRuntime(store=Store())
    with pytest.raises(LeaseLost):
        runtime._require_fill_authority()
    with pytest.raises(LeaseLost):
        asyncio.run(runtime.tick())
    runtime.market.close()


def test_reload_params_between_ticks(store_db: None) -> None:
    store = Store()
    runtime = TradingRuntime(store=store)
    runtime.params.buy_threshold = 0.42
    runtime._save_params()
    runtime.params.buy_threshold = 0.1
    runtime._reload_between_ticks()
    assert runtime.params.buy_threshold == pytest.approx(0.42)
    runtime.market.close()


def test_lease_required_to_confirm(store_db: None) -> None:
    _reset_bot_lease()
    store = Store()
    lease = LeaseManager("owner-a")
    assert lease.acquire()
    runtime = TradingRuntime(store=store, lease=lease)
    assert runtime._confirm_lease(respect_release=True) is True
    snap = read_lease()
    assert snap["held"] is True
    assert snap["owner_id"] == "owner-a"
    assert lease.release()
    assert runtime._confirm_lease(respect_release=True) is False
    runtime.market.close()


def test_persist_state_writes_exit_levels(store_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.runtime.save_state", lambda _payload: None)
    store = Store()
    runtime = TradingRuntime(store=store)
    runtime.wallet.positions["BTC-USD"] = Position(symbol="BTC-USD", qty=0.1, avg_entry=50_000.0)
    runtime._exit_levels["BTC-USD"] = {"stop_pct": 0.02, "take_pct": 0.04, "atr_pct": 0.01}
    runtime._persist_state()

    loaded = TradingRuntime(store=Store())
    assert loaded.store is not None
    assert loaded.store.load_runtime(loaded) is True
    assert loaded._exit_levels["BTC-USD"]["stop_pct"] == pytest.approx(0.02)
    assert loaded._exit_levels["BTC-USD"]["take_pct"] == pytest.approx(0.04)
    assert loaded._exit_levels["BTC-USD"]["atr_pct"] == pytest.approx(0.01)
    runtime.market.close()
    loaded.market.close()
