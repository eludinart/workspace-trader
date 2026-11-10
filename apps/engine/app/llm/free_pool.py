"""Slim Korymb-style free LLM pool: Groq → Gemini → OpenRouter :free."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import LlmSettings

logger = logging.getLogger(__name__)

_ADVANCE_STATUSES = frozenset({401, 402, 403, 404, 408, 413, 429, 500, 502, 503, 529})

_settings_lock = threading.Lock()
_settings: LlmSettings | None = None


@dataclass(frozen=True)
class PoolHop:
    id: str
    provider: str
    base_url: str
    api_key: str
    model: str
    max_tokens: int = 512


@dataclass(frozen=True)
class FreePoolResult:
    text: str
    provider: str
    model: str
    latency_ms: int
    hop_id: str
    ok: bool
    error: str | None = None


def set_llm_settings(settings: LlmSettings | None) -> None:
    """Install process-local in-app LLM settings (app fields override env)."""
    global _settings
    with _settings_lock:
        _settings = settings


def get_llm_settings() -> LlmSettings | None:
    with _settings_lock:
        return _settings


def _env(*names: str) -> str:
    for name in names:
        val = str(os.getenv(name) or "").strip()
        if val:
            return val
    return ""


def _app_or_env(app_value: str, *env_names: str) -> str:
    text = str(app_value or "").strip()
    if text:
        return text
    return _env(*env_names)


def pool_mode() -> str:
    settings = get_llm_settings()
    if settings is not None:
        app_mode = str(settings.free_pool or "").strip().lower()
        if app_mode in {"on", "off", "auto"}:
            # Only trust explicit app mode; still allow env when auto + env forces off/on
            # via LLM_FREE_POOL when app left at default auto and env is set.
            if app_mode in {"on", "off"}:
                return app_mode
    raw = str(os.getenv("LLM_FREE_POOL") or "auto").strip().lower()
    if raw in {"1", "true", "on", "yes"}:
        return "on"
    if raw in {"0", "false", "off", "no"}:
        return "off"
    if settings is not None and str(settings.free_pool or "").strip().lower() == "auto":
        return "auto"
    return "auto"


def configured_hops() -> list[PoolHop]:
    settings = get_llm_settings() or LlmSettings()
    hops: list[PoolHop] = []
    # Defaults track current free/dev availability (Aug 2026+: llama-3.1-8b retired → 404).
    groq_model = (
        _app_or_env(settings.groq_model, "GROQ_MODEL") or "openai/gpt-oss-20b"
    )
    gemini_model = (
        _app_or_env(settings.gemini_model, "GEMINI_MODEL") or "gemini-3.1-flash-lite"
    )
    or_model = (
        _app_or_env(settings.openrouter_free_model, "OPENROUTER_FREE_MODEL")
        or "deepseek/deepseek-chat-v3-0324:free"
    )

    groq_keys = [
        k
        for k in (
            _app_or_env(settings.groq_api_key, "GROQ_API_KEY"),
            _app_or_env(settings.groq_api_key_2, "GROQ_API_KEY_2"),
        )
        if k
    ]
    for i, key in enumerate(groq_keys):
        hops.append(
            PoolHop(
                id=f"groq-{i + 1}",
                provider="groq",
                base_url="https://api.groq.com/openai/v1",
                api_key=key,
                model=groq_model,
            )
        )

    gemini_key = _app_or_env(settings.gemini_api_key, "GEMINI_API_KEY")
    if gemini_key:
        hops.append(
            PoolHop(
                id="gemini",
                provider="gemini",
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                api_key=gemini_key,
                model=gemini_model,
            )
        )

    or_key = _app_or_env(settings.openrouter_api_key, "OPENROUTER_API_KEY")
    if or_key:
        hops.append(
            PoolHop(
                id="openrouter-free",
                provider="openrouter",
                base_url="https://openrouter.ai/api/v1",
                api_key=or_key,
                model=or_model,
            )
        )
    return hops


def pool_configured() -> bool:
    mode = pool_mode()
    hops = configured_hops()
    if mode == "off":
        return False
    if mode == "on":
        return True
    return len(hops) > 0


def resolve_timeout_ms(explicit: int | None = None) -> int:
    if explicit is not None:
        return max(500, int(explicit))
    settings = get_llm_settings()
    if settings is not None and int(settings.timeout_ms or 0) > 0:
        return max(500, int(settings.timeout_ms))
    try:
        return max(500, int(os.getenv("LLM_TIMEOUT_MS") or 2500))
    except ValueError:
        return 2500


def _headers(hop: PoolHop) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {hop.api_key}",
        "Content-Type": "application/json",
    }
    if hop.provider == "openrouter":
        headers["HTTP-Referer"] = _env("OPENROUTER_HTTP_REFERER") or "https://trader.eludein.art"
        headers["X-Title"] = _env("OPENROUTER_APP_TITLE") or "workspace-trader"
    return headers


def _is_reasoning_model(model: str) -> bool:
    """Groq gpt-oss / Qwen3 burn completion tokens on hidden reasoning."""
    m = (model or "").strip().lower()
    return "gpt-oss" in m or m.startswith("qwen/qwen3")


def _request_body(
    hop: PoolHop,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    """Build chat/completions body; gpt-oss needs low effort + room for answer."""
    effective = max(1, int(max_tokens))
    hop_cap = max(1, int(hop.max_tokens))
    body: dict[str, Any] = {
        "model": hop.model,
        "messages": messages,
        "temperature": temperature,
    }
    if hop.provider == "groq" and _is_reasoning_model(hop.model):
        # Hidden reasoning consumes the same max_tokens budget; 180 → empty content.
        effective = max(effective, 768)
        hop_cap = max(hop_cap, 2048)
        body["reasoning_effort"] = "low"
    body["max_tokens"] = min(effective, hop_cap)
    return body


def _extract_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    # Some OpenAI-compatible payloads return content as a list of parts.
    if isinstance(content, list):
        bits: list[str] = []
        for part in content:
            if isinstance(part, str) and part.strip():
                bits.append(part.strip())
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str) and text.strip():
                    bits.append(text.strip())
        if bits:
            return "\n".join(bits)
    return ""


def _post_hop(
    hop: PoolHop,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    timeout_ms: int,
    temperature: float,
) -> FreePoolResult:
    started = time.perf_counter()
    body = _request_body(
        hop,
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    url = f"{hop.base_url.rstrip('/')}/chat/completions"
    try:
        with httpx.Client(timeout=max(0.5, timeout_ms / 1000.0)) as client:
            res = client.post(url, headers=_headers(hop), json=body)
        latency = int((time.perf_counter() - started) * 1000)
        if res.status_code in _ADVANCE_STATUSES:
            return FreePoolResult(
                text="",
                provider=hop.provider,
                model=hop.model,
                latency_ms=latency,
                hop_id=hop.id,
                ok=False,
                error=f"http_{res.status_code}",
            )
        if res.status_code >= 400:
            return FreePoolResult(
                text="",
                provider=hop.provider,
                model=hop.model,
                latency_ms=latency,
                hop_id=hop.id,
                ok=False,
                error=f"http_{res.status_code}",
            )
        data = res.json()
        text = _extract_text(data if isinstance(data, dict) else {})
        if not text:
            finish = ""
            try:
                ch0 = (data.get("choices") or [None])[0] if isinstance(data, dict) else None
                if isinstance(ch0, dict):
                    finish = str(ch0.get("finish_reason") or "")
            except Exception:  # noqa: BLE001
                finish = ""
            err = "empty_content"
            if finish:
                err = f"empty_content:{finish}"
            return FreePoolResult(
                text="",
                provider=hop.provider,
                model=hop.model,
                latency_ms=latency,
                hop_id=hop.id,
                ok=False,
                error=err,
            )
        return FreePoolResult(
            text=text,
            provider=hop.provider,
            model=hop.model,
            latency_ms=latency,
            hop_id=hop.id,
            ok=True,
        )
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - started) * 1000)
        return FreePoolResult(
            text="",
            provider=hop.provider,
            model=hop.model,
            latency_ms=latency,
            hop_id=hop.id,
            ok=False,
            error=str(exc)[:160],
        )


def complete(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 220,
    timeout_ms: int | None = None,
    temperature: float = 0.2,
) -> FreePoolResult:
    """Try hops in order; return first ok completion or last failure."""
    timeout_ms = resolve_timeout_ms(timeout_ms)
    hops = configured_hops()
    if not hops or not pool_configured():
        return FreePoolResult(
            text="",
            provider="noop",
            model="",
            latency_ms=0,
            hop_id="noop",
            ok=False,
            error="no_keys",
        )
    last = FreePoolResult(
        text="",
        provider="",
        model="",
        latency_ms=0,
        hop_id="",
        ok=False,
        error="no_hops",
    )
    for hop in hops:
        last = _post_hop(
            hop,
            messages,
            max_tokens=max_tokens,
            timeout_ms=timeout_ms,
            temperature=temperature,
        )
        if last.ok:
            logger.info(
                "llm_pool ok hop=%s provider=%s model=%s latency_ms=%s",
                hop.id,
                hop.provider,
                hop.model,
                last.latency_ms,
            )
            return last
        logger.warning(
            "llm_pool advance hop=%s err=%s",
            hop.id,
            last.error,
        )
    return last
