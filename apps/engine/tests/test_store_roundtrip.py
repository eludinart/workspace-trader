"""Store roundtrip: cash, position, and ATR exit levels."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app import store as store_module
from app.config import AlertParams, BotParams
from app.equity_history import EquityHistory
from app.journal import DecisionJournal
from app.store import Store
from app.wallet import PaperWallet, Position


def _runtime_migrated_at() -> str | None:
    with store_module.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT migrated_at FROM runtime_meta WHERE id = %s", (1,))
            row = cur.fetchone()
    if row is None:
        return None
    value = row[0]
    return str(value) if value is not None else None


def _make_runtime() -> SimpleNamespace:
    wallet = PaperWallet(initial_balance=10_000.0, cash=8_500.0)
    wallet.positions["BTC-USD"] = Position(symbol="BTC-USD", qty=0.1, avg_entry=50_000.0)
    journal = DecisionJournal()
    journal.add(
        symbol="BTC-USD",
        action="buy",
        price=50_000.0,
        qty=0.1,
        trade_id="t1",
        confidence=0.8,
        features={"rsi": 42.0},
        top_features=[{"name": "rsi", "value": 42.0}],
        explanation="test entry",
        ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    eq = EquityHistory()
    eq.maybe_record(
        equity=10_200.0,
        cash=8_500.0,
        positions=1,
        pnl=200.0,
        peak_equity=10_200.0,
        force=True,
    )
    return SimpleNamespace(
        wallet=wallet,
        journal=journal,
        _exit_levels={
            "BTC-USD": {
                "stop_pct": 0.012,
                "take_pct": 0.024,
                "atr": 850.0,
                "atr_pct": 0.017,
            }
        },
        peak_equity=10_200.0,
        killed=False,
        kill_reason=None,
        signal_history=[{"symbol": "BTC-USD", "side": "buy"}],
        _last_buy_at={"BTC-USD": datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)},
        equity_history=eq,
    )


def test_store_runtime_roundtrip_cash_position_exit_levels(store_db: None) -> None:
    store = Store()
    original = _make_runtime()

    store.save_runtime(original)  # type: ignore[arg-type]

    loaded = _make_runtime()
    loaded.wallet = PaperWallet(initial_balance=1.0, cash=1.0)
    loaded.wallet.positions.clear()
    loaded._exit_levels.clear()
    loaded.journal = DecisionJournal()
    loaded.peak_equity = 0.0

    assert store.load_runtime(loaded) is True  # type: ignore[arg-type]

    assert loaded.wallet.cash == 8_500.0
    assert loaded.wallet.initial_balance == 10_000.0
    pos = loaded.wallet.positions["BTC-USD"]
    assert pos.qty == 0.1
    assert pos.avg_entry == 50_000.0

    levels = loaded._exit_levels["BTC-USD"]
    assert levels["stop_pct"] == 0.012
    assert levels["take_pct"] == 0.024
    assert levels["atr"] == 850.0
    assert levels["atr_pct"] == 0.017


def test_maybe_migrate_json_imports_paper_state(store_db: None, monkeypatch) -> None:
    from app.persist import import_json_to_store

    payload = {
        "peak_equity": 900.0,
        "killed": False,
        "wallet": {
            "initial_balance": 900.0,
            "cash": 450.0,
            "realized_pnl": 0.0,
            "positions": [{"symbol": "ETH-USD", "qty": 1.0, "avg_entry": 3000.0}],
            "trades": [],
        },
        "journal": [
            {
                "id": "j-migrate-1",
                "ts": "2026-01-02T10:00:00+00:00",
                "symbol": "ETH-USD",
                "action": "buy",
                "price": 3000.0,
                "qty": 1.0,
                "trade_id": "t-migrate",
                "confidence": 0.75,
                "features": {"rsi": 55.0},
                "top_features": [{"name": "rsi", "value": 55.0}],
                "explanation": "migrated journal entry",
                "simulated": False,
                "source": "live",
                "note": "",
                "narrative": "",
            }
        ],
        "equity_history": [],
        "last_buy_at": {},
        "signal_history": [],
    }
    monkeypatch.setattr("app.persist.load_state", lambda: payload)

    assert import_json_to_store() is True
    assert _runtime_migrated_at() is not None
    assert import_json_to_store() is False  # account already exists
    first_migrated_at = _runtime_migrated_at()
    assert first_migrated_at is not None

    loaded = _make_runtime()
    loaded.wallet = PaperWallet(initial_balance=1.0, cash=1.0)
    store = Store()
    assert store.load_runtime(loaded) is True  # type: ignore[arg-type]
    assert loaded.wallet.cash == 450.0
    assert "ETH-USD" in loaded.wallet.positions
    assert len(loaded.journal.entries) == 1
    entry = loaded.journal.entries[0]
    assert entry.id == "j-migrate-1"
    assert entry.symbol == "ETH-USD"
    assert entry.explanation == "migrated journal entry"

    assert store.maybe_migrate_json() is False
    assert _runtime_migrated_at() == first_migrated_at


def test_store_params_and_audit(store_db: None) -> None:
    store = Store()
    params = BotParams(initial_balance=5_000.0, symbol="ETH-USD")
    alerts = AlertParams(telegram_enabled=True, telegram_chat_id="123")

    store.save_params(params, alerts)
    loaded_params, loaded_alerts = store.load_params()
    assert loaded_params.initial_balance == 5_000.0
    assert loaded_params.symbol == "ETH-USD"
    assert loaded_alerts.telegram_enabled is True
    assert loaded_alerts.telegram_chat_id == "123"

    store.audit("cursor", "set_params", {"field": "symbol"})
