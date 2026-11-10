"""Token-secured /control routes: auth, paper mode, params, audit."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import AlertParams, BotParams
from app.control_auth import mask_secret_values
from app.journal import DecisionJournal
from app.store import Store
from app.wallet import PaperWallet

import main

_TOKEN = "test-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


def _audit_rows() -> list[tuple]:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT actor, action, payload FROM control_audit ORDER BY id"
            )
            return list(cur.fetchall())


def _reset_memory() -> None:
    runtime = main.runtime
    runtime.params = BotParams()
    runtime.alerts.params = AlertParams()
    runtime.wallet = PaperWallet(initial_balance=runtime.params.initial_balance)
    runtime.killed = False
    runtime.kill_reason = None
    runtime.peak_equity = runtime.params.initial_balance
    runtime._exit_levels.clear()
    runtime.journal = DecisionJournal()


@pytest.fixture
def client(store_db: None, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADER_CONTROL_TOKEN", _TOKEN)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr("main.run_migrations", lambda: [])
    monkeypatch.setattr("app.store.Store.maybe_migrate_json", lambda self: False)
    monkeypatch.setattr("app.runtime.save_state", lambda _payload: None)
    with TestClient(main.app) as test_client:
        _reset_memory()
        yield test_client


def test_mask_secret_values() -> None:
    masked = mask_secret_values({"buy_threshold": 0.5, "smtp_password": "hunter2", "note": ""})
    assert masked["buy_threshold"] == 0.5
    assert masked["smtp_password"] == "***"
    assert masked["note"] == ""


def test_missing_or_wrong_token_is_401(client: TestClient) -> None:
    assert client.get("/control/status").status_code == 401
    bad = client.put(
        "/control/params",
        headers={"Authorization": "Bearer wrong"},
        json={"buy_threshold": 0.66},
    )
    assert bad.status_code == 401
    assert bad.json()["detail"] == "unauthorized"
    assert _audit_rows() == []


def test_unset_token_is_401_and_production_refuses_mutate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TRADER_CONTROL_TOKEN", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    dev = client.put("/control/params", headers=_AUTH, json={"buy_threshold": 0.66})
    assert dev.status_code == 401
    assert dev.json()["detail"] == "unauthorized"

    monkeypatch.setenv("APP_ENV", "production")
    prod = client.put("/control/params", headers=_AUTH, json={"buy_threshold": 0.66})
    assert prod.status_code == 401
    assert prod.json()["detail"] == "control token unset"
    read = client.get("/control/status", headers=_AUTH)
    assert read.status_code == 401
    assert read.json()["detail"] == "unauthorized"
    assert _audit_rows() == []


def test_put_params_validates_persists_and_audits(client: TestClient) -> None:
    denied = client.put("/control/params", headers=_AUTH, json={"buy_threshold": 0.1})
    assert denied.status_code == 422
    assert _audit_rows() == []

    updated = client.put(
        "/control/params",
        headers={**_AUTH, "X-Control-Actor": "ui"},
        json={"buy_threshold": 0.66},
    )
    assert updated.status_code == 200
    assert updated.json()["buy_threshold"] == pytest.approx(0.66)

    fetched = client.get("/control/params", headers=_AUTH)
    assert fetched.status_code == 200
    assert fetched.json()["buy_threshold"] == pytest.approx(0.66)

    stored, _alerts, _llm = Store().load_params()
    assert stored.buy_threshold == pytest.approx(0.66)

    rows = _audit_rows()
    assert len(rows) == 1
    actor, action, payload = rows[0]
    assert actor == "ui"
    assert action == "params"
    assert payload["diff"]["buy_threshold"]["to"] == pytest.approx(0.66)


def test_status_kill_resume_and_tick(client: TestClient) -> None:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO account (
                  id, mode, cash, initial_balance, realized_pnl, peak_equity, killed
                ) VALUES (%s, 'paper', %s, %s, %s, %s, %s)
                """,
                (1, 2500.0, 2500.0, 0.0, 2500.0, False),
            )
            cur.execute(
                """
                INSERT INTO positions (symbol, qty, avg_entry, exit_meta)
                VALUES (%s, %s, %s, %s)
                """,
                ("ETH-USD", 1.5, 3000.0, "{}"),
            )
        conn.commit()

    status = client.get("/control/status", headers=_AUTH)
    assert status.status_code == 200
    body = status.json()
    assert body["mode"] == "paper"
    assert body["cash"] == pytest.approx(2500.0)
    assert body["killed"] is False
    assert body["positions"] == [{"symbol": "ETH-USD", "qty": 1.5, "avg_entry": 3000.0}]
    assert body["lease"]["held"] is False
    assert "owner_id" in body["lease"]
    assert "heartbeat_at" in body["lease"]

    killed = client.post("/control/kill", headers=_AUTH, json={"reason": "cursor"})
    assert killed.status_code == 200
    assert killed.json()["killed"] is True
    assert killed.json()["auto_trade"] is False

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT killed, kill_reason FROM account WHERE id = %s", (1,))
            flag, reason = cur.fetchone()
    assert flag is True
    assert reason == "cursor"

    resumed = client.post("/control/resume", headers=_AUTH)
    assert resumed.status_code == 200
    assert resumed.json()["killed"] is False

    tick = client.post("/control/tick", headers=_AUTH)
    assert tick.status_code == 409
    assert "tick refused" in tick.json()["detail"]

    actions = [row[1] for row in _audit_rows()]
    assert actions == ["kill", "resume", "tick"]
    assert _audit_rows()[-1][2]["accepted"] is False


def test_non_paper_mode_is_refused(client: TestClient) -> None:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO account (
                  id, mode, cash, initial_balance, realized_pnl, peak_equity, killed
                ) VALUES (%s, 'live', %s, %s, %s, %s, %s)
                """,
                (1, 100.0, 100.0, 0.0, 100.0, False),
            )
        conn.commit()

    refused = client.post("/control/kill", headers=_AUTH, json={"reason": "nope"})
    assert refused.status_code == 403
    assert refused.json()["detail"] == "paper mode required"
    status = client.get("/control/status", headers=_AUTH)
    assert status.status_code == 403
    assert _audit_rows() == []

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT killed FROM account WHERE id = %s", (1,))
            assert cur.fetchone()[0] is False


def test_bad_actor_is_400(client: TestClient) -> None:
    res = client.post(
        "/control/resume",
        headers={**_AUTH, "X-Control-Actor": "root"},
    )
    assert res.status_code == 400
    assert _audit_rows() == []


def test_audit_failure_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args, **_kwargs) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(main.runtime.store, "audit", _boom)
    res = client.put("/control/params", headers=_AUTH, json={"buy_threshold": 0.66})
    assert res.status_code == 503
    assert res.json()["detail"] == "control write failed"
