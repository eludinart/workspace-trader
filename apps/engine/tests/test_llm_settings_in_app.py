"""In-app LlmSettings: mask, app-over-env, store roundtrip."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from app.config import AlertParams, BotParams, LlmSettings
from app.llm.free_pool import configured_hops, set_llm_settings
from app.store import Store


@pytest.fixture(autouse=True)
def _clear_llm_settings() -> None:
    set_llm_settings(None)
    yield
    set_llm_settings(None)


def test_app_key_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "env-groq")
    set_llm_settings(LlmSettings(groq_api_key="app-groq"))
    hops = configured_hops()
    assert hops
    assert hops[0].provider == "groq"
    assert hops[0].api_key == "app-groq"


def test_empty_app_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "env-only")
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    set_llm_settings(LlmSettings())
    hops = configured_hops()
    assert len(hops) == 1
    assert hops[0].api_key == "env-only"


def test_store_llm_roundtrip(store_db: None) -> None:
    store = Store()
    llm = LlmSettings(
        groq_api_key="gsk_test",
        gemini_model="gemini-2.0-flash",
        free_pool="on",
        timeout_ms=3000,
    )
    store.save_params(BotParams(), AlertParams(), llm)
    _p, _a, loaded = store.load_params()
    assert loaded.groq_api_key == "gsk_test"
    assert loaded.gemini_model == "gemini-2.0-flash"
    assert loaded.free_pool == "on"
    assert loaded.timeout_ms == 3000


@pytest.fixture
def llm_client(store_db: None, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADER_CONTROL_TOKEN", "test-token")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr("main.run_migrations", lambda: [])
    monkeypatch.setattr("app.store.Store.maybe_migrate_json", lambda self: False)
    monkeypatch.setattr("app.runtime.save_state", lambda _payload: None)
    with TestClient(main.app) as test_client:
        main.runtime.params = BotParams()
        main.runtime.alerts.params = AlertParams()
        main.runtime.llm = LlmSettings()
        set_llm_settings(main.runtime.llm)
        yield test_client


def test_llm_params_mask_and_preserve_star(llm_client: TestClient) -> None:
    put = llm_client.put(
        "/llm/params",
        json={"groq_api_key": "secret-one", "timeout_ms": 2600},
    )
    assert put.status_code == 200
    body = put.json()
    assert body["groq_api_key"] == "***"
    assert body["timeout_ms"] == 2600

    again = llm_client.put(
        "/llm/params",
        json={"groq_api_key": "***", "timeout_ms": 2700},
    )
    assert again.status_code == 200
    assert again.json()["groq_api_key"] == "***"
    assert again.json()["timeout_ms"] == 2700

    got = llm_client.get("/llm/params")
    assert got.status_code == 200
    assert got.json()["groq_api_key"] == "***"
    assert main.runtime.llm.groq_api_key == "secret-one"
