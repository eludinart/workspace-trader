"""API process must not fill; trader persists exit levels and reloads params."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone

import pytest

from app import db
from app.lease import LeaseManager, read_lease
from app.runtime import LeaseLost, TradingRuntime
from app.store import Store
from app.wallet import Position, Trade


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


def _seed_trade(runtime: TradingRuntime) -> None:
    runtime.last_price = 50_000.0
    runtime.wallet.positions["BTC-USD"] = Position(symbol="BTC-USD", qty=0.1, avg_entry=50_000.0)
    runtime.wallet.trades.append(
        Trade(
            id="t-seed",
            symbol="BTC-USD",
            side="buy",
            qty=0.1,
            price=50_000.0,
            fee=1.0,
            ts=datetime.now(timezone.utc),
            note="seed",
        )
    )


def test_api_reset_flags_book_until_trader_applies(
    store_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.runtime.save_state", lambda _payload: None)
    monkeypatch.setattr("app.runtime.clear_state", lambda: None)
    monkeypatch.setattr(
        "app.market.coinbase.CoinbaseClient.get_ticker",
        lambda _self, _symbol: {"price": "50000"},
    )
    _reset_bot_lease()
    store = Store()
    lease = LeaseManager("owner-reset")
    assert lease.acquire()
    owner = TradingRuntime(store=store, lease=lease)
    _seed_trade(owner)
    owner._persist_state()

    saves = {"n": 0}
    real_save = Store.save_runtime

    def _counting_save(self: Store, runtime: TradingRuntime) -> None:
        saves["n"] += 1
        real_save(self, runtime)

    monkeypatch.setattr(Store, "save_runtime", _counting_save)

    api = TradingRuntime(store=Store())
    api.last_price = 50_000.0
    result = api.reset_account(12_500.0)
    assert result["reset_accepted"] is True
    assert result["reset_requested"] is True
    assert result["trade_count"] == 1
    assert saves["n"] == 0

    requested, balance = store.peek_reset_request()
    assert requested is True
    assert balance == pytest.approx(12_500.0)
    mid = TradingRuntime(store=Store())
    assert mid.store is not None and mid.store.load_runtime(mid) is True
    assert len(mid.wallet.trades) == 1
    assert "BTC-USD" in mid.wallet.positions

    owner._persist_state()
    assert store.peek_reset_request()[0] is True
    after_tick = TradingRuntime(store=Store())
    assert after_tick.store is not None and after_tick.store.load_runtime(after_tick) is True
    assert len(after_tick.wallet.trades) == 1

    owner._apply_pending_reset()
    done = TradingRuntime(store=Store())
    assert done.store is not None and done.store.load_runtime(done) is True
    assert done.wallet.trades == []
    assert done.wallet.positions == {}
    assert done.wallet.initial_balance == pytest.approx(12_500.0)
    assert store.peek_reset_request() == (False, None)

    for runtime in (owner, api, mid, after_tick, done):
        runtime.market.close()


def test_stop_waits_for_inflight_tick(store_db: None) -> None:
    runtime = TradingRuntime(store=Store())
    release = threading.Event()
    order: list[str] = []

    def blocker() -> None:
        order.append("tick-entered")
        release.wait(timeout=5)
        order.append("tick-done")

    async def inflight() -> None:
        async with runtime._lock:
            await asyncio.to_thread(blocker)

    async def scenario() -> None:
        runtime.running = True
        runtime._task = asyncio.create_task(inflight())
        for _ in range(50):
            if order:
                break
            await asyncio.sleep(0.01)
        assert order == ["tick-entered"]
        stop_task = asyncio.create_task(runtime.stop())
        await asyncio.sleep(0.05)
        assert stop_task.done() is False
        release.set()
        await asyncio.wait_for(stop_task, timeout=2)
        assert order == ["tick-entered", "tick-done"]

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        runtime.market.close()


def test_persist_after_release_does_not_save(
    store_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.runtime.save_state", lambda _payload: None)
    _reset_bot_lease()
    lease = LeaseManager("owner-release")
    assert lease.acquire()
    runtime = TradingRuntime(store=Store(), lease=lease)
    _seed_trade(runtime)
    runtime._persist_state()
    assert lease.release()
    runtime.wallet.trades.clear()
    runtime.wallet.positions.clear()
    with pytest.raises(LeaseLost):
        runtime._persist_state()
    with pytest.raises(LeaseLost):
        runtime._sell_qty("BTC-USD", 0.1, 50_000.0, "exit:signal")
    loaded = TradingRuntime(store=Store())
    assert loaded.store is not None and loaded.store.load_runtime(loaded) is True
    assert len(loaded.wallet.trades) == 1
    assert loaded.wallet.trades[0].id == "t-seed"
    assert "BTC-USD" in loaded.wallet.positions
    runtime.market.close()
    loaded.market.close()
