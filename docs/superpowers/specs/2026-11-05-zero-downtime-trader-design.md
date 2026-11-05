# Zero-downtime paper trader — design

Date: 2026-11-05  
Status: approved (conversation) — pending user review of this file  
Repo: `workspace-trader`  
Deploy: Coolify UUID `onx2blngujkokmltpugysuh4` → https://trader.eludein.art

## Goal

Update the application on the VPS **without interrupting ongoing paper trades**, and keep **all application state** so the bot never loses the thread of open actions. The system must be hardened now for an eventual **real crypto portfolio** (fail-closed, durable state, secret hygiene), while remaining **paper-only** in this phase.

## Decisions (locked)

| Topic | Choice |
|-------|--------|
| Continuity model | Continuous trading during trader upgrades (lease handoff), not “brief stop is OK” |
| Process split | **A**: long-lived `trader` worker + redeployable `engine` API + `web` |
| State store | **Postgres** (not JSON-as-master, not SQLite) |
| Postgres hosting | **Existing Coolify standalone Postgres** (`pb12t523fbppov71muhtlaz3`) — dedicated DB/user `trader`; do **not** use `coolify-db` |
| Handoff | **Lease + heartbeat** (single book owner); not dual-active blue/green |
| Mode | `paper` only for now; `mode` flag in DB for future live |
| Cursor ↔ app | **MCP `trader`** over a **secured control API** (token); skill as same-API fallback |

## Current baseline (verified 2026-11-05)

- Stack: `web` (Next.js) + `engine` (FastAPI) in one compose; engine starts the trading loop in process lifespan.
- Persistence: `paper_state.json` on Docker volume `onx2blngujkokmltpugysuh4_engine-data` (atomic write via `.tmp` replace).
- Gap: in-memory `_exit_levels` (TP/SL locked at entry) are **not** in the JSON payload today — must be persisted in Postgres.
- RAM on VPS is constrained; avoid a second Postgres container in the trader compose.

## Architecture

```
Browser → web (:3100) → proxy /api/engine/* → engine (FastAPI, no trade loop)
                                              ↓
                                         Postgres (Coolify)
                                              ↑
                                         trader (worker, sole order executor)
```

### Service roles

| Service | Image | Responsibility |
|---------|-------|----------------|
| `web` | `apps/web` | UI / PWA; proxies to engine; free to redeploy anytime |
| `engine` | `apps/engine` | HTTP API: account, journal, params, kill/resume, backtest, health; **does not** run tick/price/news loops |
| `trader` | same as `engine`, different CMD | Acquires lease; runs loops; executes paper fills; writes book to Postgres |

### Hard rules

1. Only `trader` may create fills (paper buys/sells).
2. Postgres is the **source of truth**; process memory is a cache rebuilt on acquire.
3. At most one lease owner; ambiguous lease or DB unavailable → **fail-closed** (no orders).
4. Secrets (Telegram, future exchange keys) live in Coolify env only — never plaintext in DB or git.
5. `mode=paper` until an explicit live cutover (out of scope for this delivery).
6. Strategy adjustments from Cursor go through the **control API** (never direct DB writes, never redeploy for params).

## Cursor control channel

Strategy tuning happens in Cursor with the agent. Redeploys are the wrong path for param tweaks.

```
Cursor (MCP trader / skill) --HTTPS + Bearer token--> engine control API --> bot_params / account (Postgres)
                                                                      --> trader reloads between ticks
```

### Control API (on `engine`)

- Auth: `Authorization: Bearer $TRADER_CONTROL_TOKEN` (Coolify env; never in git).
- Mutating routes require the token; read-only market/account may stay as today or also lock behind token in prod (prefer **token on all mutating + sensitive reads**).
- Core operations:
  - `GET /control/status` — mode, lease owner/heartbeat, killed, cash, positions summary
  - `GET /control/params` — current bot params (secrets masked)
  - `PUT /control/params` — partial update; validated by existing `BotParams` rules; persisted to `bot_params`
  - `POST /control/kill` / `POST /control/resume`
  - `POST /control/tick` — optional forced decision (paper)
- Every mutation appends an **audit** row (`actor=cursor|ui`, `action`, `payload_diff`, `ts`).
- Trader polls/reloads params at safe points (between ticks); no process restart required.

### MCP server (`trader`)

- Thin MCP wrapping the control API (typed tools matching the routes above).
- Runs where Cursor can reach prod (`https://trader.eludein.art` or engine URL behind the web proxy).
- Config: base URL + token via Cursor MCP env (not committed).
- Fallback: Cursor skill calling the same HTTP API if MCP is down.

### Hard rules (control plane)

1. MCP/skill **never** executes arbitrary SQL or shell on the VPS.
2. No live order placement endpoints in this phase (`mode=paper` enforced server-side).
3. Invalid or missing token → 401; DB/lease failure on mutate → fail-closed, no partial silent apply.
4. UI dashboard may keep using existing routes; control routes are the **supported** agent path.

## Data model

Database name: `trader` (dedicated role with rights limited to this DB).

| Table | Purpose |
|-------|---------|
| `schema_migrations` | Applied migration ids |
| `bot_lease` | Single-row mutex: `owner_id`, `heartbeat_at`, `expires_at`, `release_requested`, `version` |
| `account` | `cash`, `initial_balance`, `realized_pnl`, `peak_equity`, `killed`, `kill_reason`, `session_started_at`, `mode` |
| `positions` | `symbol`, `qty`, `avg_entry`, **exit level fields** (`stop_pct`, `take_pct`, ATR metadata as needed) |
| `trades` | Full trade history (no silent truncate-to-200 as sole storage) |
| `journal` | Decision journal rows (features + narrative fields) |
| `equity_points` | Equity curve samples |
| `bot_params` | Bot + alert **non-secret** params (JSON); tokens stay in env |
| `runtime_meta` | `last_buy_at`, short `signal_history`, migration markers |
| `control_audit` | Cursor/UI mutations: actor, action, diff, ts |

### Lease / handoff protocol

1. On start, `trader` tries to acquire `bot_lease` if free or expired (TTL ~15–30s), with monotonic `version`.
2. While running, refresh `heartbeat_at` / `expires_at` every tick (or fixed interval shorter than TTL).
3. Before each tick: confirm `owner_id == self`; else exit cleanly after finishing no new orders.
4. On deploy of a new `trader`: new instance starts; may set `release_requested` or wait for expiry; old instance sees signal/owner change, finishes current tick, releases, exits.
5. New owner loads full state from Postgres, then resumes loops.
6. Never two active executors: if acquire races, DB uniqueness / version check decides; loser exits without trading.

### Migration from JSON

1. Keep volume `engine_data` mounted (read) during cutover.
2. One-shot: if `account` empty and `paper_state.json` present → import in a **single transaction** → set `runtime_meta.migrated_at`.
3. Retain JSON on volume as backup for several days; stop treating it as master.
4. Import must include wallet, journal, equity, kill flags, last_buy_at — and leave exit levels empty only if absent in source (then recompute on next entry; open positions without stored levels get conservative defaults documented in implementation).

## Deploy (Coolify)

- Compose services: `web`, `engine`, `trader`; external `DATABASE_URL`.
- Redeploy **web/engine** for UI/API changes → trader untouched → no handoff.
- Redeploy **trader** only when trading logic changes → lease handoff.
- Health:
  - `engine`: HTTP `/health` includes DB ping
  - `trader`: heartbeat freshness visible via API (`lease.owner_id`, `heartbeat_at`) and container healthcheck
- Smoke after deploy: `https://trader.eludein.art/`, `/api/engine/health`, account positions match pre-deploy snapshot

## Security / live-ready posture (paper phase)

- Fail-closed on lease ambiguity or DB errors during order path.
- Kill switch persisted in `account`; both API writes and trader reads every tick.
- No exchange write keys in this phase.
- Parameter updates via control API write `bot_params`; trader reloads at safe points (between ticks).
- Do not log secrets; mask tokens in API responses (existing behavior).
- `TRADER_CONTROL_TOKEN` required for agent/control mutations; rotate via Coolify without rebuild when possible.

## Out of scope (this design)

- Live broker / real order routing
- Multi-VPS or multi-region active-active
- Replacing Coolify Postgres with an in-compose Postgres
- Full audit log UI (tables can support it later)

## Success criteria

1. Redeploy `web` or `engine` while positions stay open and trader keeps ticking.
2. Redeploy `trader`: handoff completes without duplicate fills; cash/positions/journal continuous.
3. Cold start after crash restores full book from Postgres (including exit levels once persisted).
4. Migration from current prod `paper_state.json` loses no open positions or cash.
5. If Postgres is unreachable, trader does not place paper orders.

## Implementation sketch (for later plan)

1. Provision DB/user on Coolify Postgres; set `DATABASE_URL` on trader app.
2. Add migrations + repository layer in `apps/engine`.
3. Split lifespan: API-only vs `trader_main` worker.
4. Update `docker-compose.yml` with `trader` service.
5. Migrate persist path JSON → Postgres; one-shot importer.
6. Persist `_exit_levels` with positions.
7. Lease acquire/heartbeat/release + deploy docs.
8. Control API + `control_audit` + `TRADER_CONTROL_TOKEN`.
9. MCP `trader` (+ optional Cursor skill fallback) wired to prod URL.
10. Prod cutover: migrate volume → verify → redeploy services.
