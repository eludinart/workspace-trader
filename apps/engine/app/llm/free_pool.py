"""Slim Korymb-style free LLM pool: Groq → Gemini → OpenRouter :free."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_ADVANCE_STATUSES = frozenset({401, 402, 403, 404, 408, 413, 429, 500, 502, 503, 529})


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


def _env(*names: str) -> str:
    for name in names:
        val = str(os.getenv(name) or "").strip()
        if val:
            return val
    return ""


def pool_mode() -> str:
    raw = str(os.getenv("LLM_FREE_POOL") or "auto").strip().lower()
    if raw in {"1", "true", "on", "yes"}:
        return "on"
    if raw in {"0", "false", "off", "no"}:
        return "off"
    return "auto"


def configured_hops() -> list[PoolHop]:
    hops: list[PoolHop] = []
    groq_model = _env("GROQ_MODEL") or "llama-3.1-8b-instant"
    gemini_model = _env("GEMINI_MODEL") or "gemini-2.0-flash"
    or_model = _env("OPENROUTER_FREE_MODEL") or "deepseek/deepseek-chat-v3-0324:free"

    groq_keys = [k for k in (_env("GROQ_API_KEY"), _env("GROQ_API_KEY_2")) if k]
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

    gemini_key = _env("GEMINI_API_KEY")
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

    or_key = _env("OPENROUTER_API_KEY")
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


def _headers(hop: PoolHop) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {hop.api_key}",
        "Content-Type": "application/json",
    }
    if hop.provider == "openrouter":
        headers["HTTP-Referer"] = _env("OPENROUTER_HTTP_REFERER") or "https://trader.eludein.art"
        headers["X-Title"] = _env("OPENROUTER_APP_TITLE") or "workspace-trader"
    return headers


def _extract_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
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
    body = {
        "model": hop.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": min(max_tokens, hop.max_tokens),
    }
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
            return FreePoolResult(
                text="",
                provider=hop.provider,
                model=hop.model,
                latency_ms=latency,
                hop_id=hop.id,
                ok=False,
                error="empty_content",
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
    if timeout_ms is None:
        try:
            timeout_ms = int(os.getenv("LLM_TIMEOUT_MS") or 2500)
        except ValueError:
            timeout_ms = 2500
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
