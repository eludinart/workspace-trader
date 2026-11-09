# Live LLM Co-pilot — Design

**Date:** 2026-11-09  
**Status:** draft — awaiting user review  
**Scope:** Paper live only. XGBoost remains the proposer; an LLM co-pilot may veto, resize, or force-exit within hard risk caps.

## Goal

Connect one or more LLMs so they work **live** on entry/exit decisions and open positions — as a **co-pilot**, not an autonomous pilot.

Success looks like:

- Before a proposed buy/sell, the co-pilot can `approve` | `veto` | `resize` (within caps).
- Open positions are reviewed on a cadence; the co-pilot may `force_exit` with a French reason.
- Soft-fail: timeout / provider error → XGBoost-only path continues; journal notes the miss.
- Providers are pluggable; v1 tests with **Groq** + free secondaries (same idea as Korymb).

## Precedent: Korymb free pool

Do **not** invent a second LLM stack. Reuse the pattern from Korymb (`backend/llm_free_pool.py` + `llm_client.py`):

- Ordered **hops** (provider + model + key): Groq models → Gemini → OpenRouter `:free`.
- Failover on daily quota / 429 / model errors; advance to next hop.
- Keys from env (`GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, …).
- OpenAI-compatible `/chat/completions` where possible.

Trader v1 ports a **slim** copy under `apps/engine/app/llm/` (no Korymb admin UI, no paid tiers). Behavior and env names stay familiar so Éric can reuse the same Groq/Gemini keys mentally.

## Non-goals (v1)

- LLM as sole decision-maker (replace XGBoost).
- Live money / exchange write keys.
- Multi-agent tool use, browsing, or arbitrary code execution.
- Changing kill-switch, max portfolio risk R, liquidity gates, or fee twin math.

## Role split

| Layer | Owns |
|-------|------|
| Universe + liquidity filters | Which symbols may trade |
| XGBoost + scan bias + news thresholds | Proposed `buy` / `hold` / `sell` + confidence |
| Risk caps (size %, stop, open R, max symbols) | Hard ceilings — **not overridable by LLM** |
| Protective TP/SL | Mechanical exits every tick |
| **LLM co-pilot** | Soft judgment: veto / resize / force_exit + `reason_fr` |
| Kill switch | Absolute halt |

## When the co-pilot runs (hybrid)

1. **Pre-trade:** after XGBoost/gates propose `buy` or `sell`, before `_build_fill_quote` / wallet mutate.
2. **Open-book review:** every N minutes (default **3**), for each open position — independent of signal.
3. **Not** on every hold tick (cost/latency).

Flag: `copilot_enabled` (param + env). Default **off** until keys exist; paper can enable via `/params` or control API.

## Provider chain (v1) — Korymb-style free pool

Config via Coolify env (never commit secrets):

| Env | Role |
|-----|------|
| `COPILOT_ENABLED` | Bootstrap on/off |
| `LLM_FREE_POOL` | `auto` / `on` / `off` (default `auto`: on if any key set) |
| `GROQ_API_KEY` | Primary free hop(s) |
| `GROQ_API_KEY_2` | Optional second Groq key (same models, extra quota) |
| `GEMINI_API_KEY` | Secondary free hop |
| `OPENROUTER_API_KEY` | Tertiary `:free` models |
| `GROQ_MODEL` / `GEMINI_MODEL` / `OPENROUTER_FREE_MODEL` | Optional overrides |
| `LLM_TIMEOUT_MS` | Default `2500` (copilot must stay fast) |

**Default hop order (mirrors Korymb lite path):**

1. Groq (fast small model) — key then `GROQ_API_KEY_2` if set  
2. Gemini Flash-lite class  
3. OpenRouter free model  

Soft-fail if the pool is empty or all hops fail → XGBoost-only.

Interface (engine):

```text
FreePool.complete(messages, *, max_tokens, timeout_ms) -> { text, provider, model, latency_ms }
```

Stub when no keys: noop → soft-fail (trading path unchanged).

## Co-pilot I/O contract

**Input (compact JSON in the user message):** symbol, side proposed, confidence, probs, RSI/MACD snapshot, news bias + top headline, position (qty, avg, uPnL%), equity/cash, distance to TP/SL, estimated RT friction bps, style, open symbol count.

**Output (strict JSON only):**

```json
{
  "action": "approve" | "veto" | "resize" | "force_exit",
  "size_mult": 1.0,
  "reason_fr": "…"
}
```

Rules:

- `resize`: `size_mult` in `[0.25, 1.0]` only (never above XGBoost/risk proposal).
- `force_exit` only valid on open-book review (or when reviewing an existing position during a sell path).
- Unknown / invalid JSON → soft-fail = `approve` with `size_mult=1` and journal `copilot_parse_error`.
- French `reason_fr` max ~240 chars; shown in journal + decision narrative.

System prompt: short, paper-trading co-pilot, respect friction vs edge, prefer veto when edge < ~RT cost.

## Runtime integration

In `TradingRuntime._execute_signal` / protect path:

1. Build proposal as today (gates, spend, levels).
2. If `copilot_enabled` and proposal is actionable → `CopilotService.review_entry(...)`.
3. Apply result: veto → hold; resize → scale spend; approve → unchanged.
4. After fill → existing post-trade hook can attach copilot reason (optional).

Separate lightweight timer or tick-side check: if `now - last_copilot_book_review > interval` → review each open position once.

Publish via existing live bus: narrative includes `Co-pilote : {reason_fr}`.

## UI / observability

- Decision journal line includes co-pilot action + reason.
- System status feature `copilot`: ok / warn (no keys) / error (all providers failing).
- Params: `copilot_enabled`, `copilot_review_interval_sec` (default 180).

No chat UI in v1.

## Failure & safety

- Timeout / 429 / 5xx → next key/provider → then soft-fail.
- Rate-limit: max 1 copilot call per symbol per 20s (entry); book review batched.
- Never increase size above risk engine; never disable kill switch.
- Logs: provider id used, latency_ms, action — no API keys.

## Testing

- Unit: JSON parse / clamp `size_mult` / invalid → soft-fail.
- Unit: provider chain failover (mock HTTP).
- Integration: with `NoopProvider`, trading path unchanged.
- Manual paper: enable Groq key on Coolify, one forced signal path, confirm journal.

## Rollout

1. Land adapters + `CopilotService` + flag **off**.
2. Set `GROQ_API_KEY` (+ optional `_2` / fallbacks) on Coolify; enable flag on paper.
3. Watch veto rate and latency for 1–2 days before tightening prompts.

## Open choices (locked for v1)

- Mode: **co-pilot** (not veto-only, not full pilot).
- Cadence: **hybrid** (pre-trade + periodic open review).
- Primary LLM: **Groq**; secondaries: free keys via same chain.
- Provider pick beyond Groq: deferred; interface ready.
