"""Free-pool hop selection + failover."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from app.llm import free_pool


def test_configured_hops_order_groq_then_gemini(monkeypatch) -> None:
    free_pool.set_llm_settings(None)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("GROQ_API_KEY_2", "gsk_test2")
    monkeypatch.setenv("GEMINI_API_KEY", "gem_test")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    hops = free_pool.configured_hops()
    assert [h.id for h in hops] == ["groq-1", "groq-2", "gemini"]
    assert hops[0].model == "openai/gpt-oss-20b"
    assert hops[2].model == "gemini-3.1-flash-lite"
    assert free_pool.pool_configured() is True


def test_complete_advances_on_429(monkeypatch) -> None:
    free_pool.set_llm_settings(None)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_a")
    monkeypatch.setenv("GEMINI_API_KEY", "gem_b")
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    calls: list[str] = []

    class FakeResp:
        def __init__(self, status: int, payload: dict):
            self.status_code = status
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            calls.append(url)
            if "groq" in url:
                return FakeResp(429, {"error": "rate"})
            return FakeResp(
                200,
                {"choices": [{"message": {"content": '{"action":"approve","size_mult":1,"reason_fr":"ok"}'}}]},
            )

    with patch.object(free_pool.httpx, "Client", FakeClient):
        result = free_pool.complete([{"role": "user", "content": "hi"}], timeout_ms=1000)

    assert result.ok is True
    assert result.provider == "gemini"
    assert len(calls) == 2
