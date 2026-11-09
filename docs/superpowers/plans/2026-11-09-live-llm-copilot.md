# Live LLM Co-pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a paper-live LLM co-pilot (approve / veto / resize / force_exit) backed by a Korymb-style free pool (Groq → Gemini → OpenRouter).

**Architecture:** Slim `app/llm/` free-pool client + `CopilotService` that wraps XGBoost proposals; soft-fail preserves today’s path. Wired in `TradingRuntime` pre-trade and periodic open-book review.

**Tech Stack:** Python FastAPI engine, httpx, env keys on Coolify, existing journal/live bus.

## Global Constraints

- Paper only; never override kill switch / max portfolio risk / liquidity gates.
- `size_mult` clamped to `[0.25, 1.0]`; invalid JSON → soft-fail approve.
- No secrets in repo; keys via `GROQ_API_KEY`, `GROQ_API_KEY_2`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`.
- Spec: `docs/superpowers/specs/2026-11-09-live-llm-copilot-design.md`.

---

### Task 1: Free-pool client (slim)

**Files:**
- Create: `apps/engine/app/llm/__init__.py`
- Create: `apps/engine/app/llm/free_pool.py`
- Create: `apps/engine/tests/test_llm_free_pool.py`

- [ ] Failing tests: hop order with Groq key only; failover on 429 mock
- [ ] Implement `complete(messages) -> {text, provider, model, latency_ms}`
- [ ] Commit

### Task 2: Copilot parse + service

**Files:**
- Create: `apps/engine/app/llm/copilot.py`
- Create: `apps/engine/tests/test_copilot.py`

- [ ] Tests: parse approve/veto/resize clamp / invalid → soft-fail
- [ ] `CopilotService.review_entry` / `review_position`
- [ ] Commit

### Task 3: Config + runtime wire

**Files:**
- Modify: `apps/engine/app/config.py` (`copilot_enabled`, `copilot_review_interval_sec`)
- Modify: `apps/engine/main.py` `ParamsUpdate`
- Modify: `apps/engine/app/runtime.py` (pre-trade + book review + status)
- Modify: `.env.example`

- [ ] Default `copilot_enabled=False`; env `COPILOT_ENABLED` can bootstrap True
- [ ] Soft-fail path leaves fills unchanged
- [ ] System status feature `copilot`
- [ ] Commit

### Task 4: Deploy notes

- [ ] Document Coolify env vars in `.env.example`
- [ ] Manual: set Groq key, enable flag, smoke one decision narrative
