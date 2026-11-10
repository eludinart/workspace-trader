# LLM settings in-app — Design

**Date:** 2026-11-09  
**Status:** approved  
**Scope:** Paper trader. Configure free-pool LLM API keys and models from the dashboard and API, without requiring Coolify env vars.

## Goal

Operators can paste Groq / Gemini / OpenRouter keys (and optional model overrides) in the app. Coolify env remains an optional fallback when an in-app field is empty.

## Decisions

| Choice | Locked |
|--------|--------|
| Surface | Dashboard UI **and** `GET/PUT /llm/params` |
| Precedence | Non-empty app field wins; else env |
| Storage | Dedicated `LlmSettings` JSON on `bot_params.llm` (same trust model as Telegram token) |
| Masking | Read returns `***` for secrets; write ignores `***` so a re-save does not wipe keys |
| Copilot flag | Stays on `BotParams` (`copilot_enabled`, `copilot_review_interval_sec`) |

## Data model (`LlmSettings`)

- Secrets: `groq_api_key`, `groq_api_key_2`, `gemini_api_key`, `openrouter_api_key`
- Overrides: `groq_model`, `gemini_model`, `openrouter_free_model`, `timeout_ms` (default 2500), `free_pool` (`auto` | `on` | `off`)

## Persistence

- Migration `007_bot_params_llm.sql`: add `llm JSONB NOT NULL DEFAULT '{}'`
- `Store.save_params` / `load_params` round-trip params + alerts + llm

## Runtime resolution

`free_pool` reads a process-local `LlmSettings` set by `TradingRuntime` on hydrate/update. For each key/model/timeout/mode: use stripped app value if present, otherwise `os.getenv`.

## API / UI

- Engine: masked `GET/PUT /llm/params`
- Web: Co-pilote LLM panel (keys, models, timeout, free_pool) + optional copilot enable via `/params`

## Non-goals

Encryption vault, multi-user ACL, live exchange keys, chat UI.
