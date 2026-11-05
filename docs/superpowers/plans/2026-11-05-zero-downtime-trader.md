# Zero-downtime trader + Cursor control — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split API and trading worker, persist all paper state in Coolify Postgres with lease handoff, and expose a token-secured control API + MCP so Cursor can tune strategy without redeploying.

**Architecture:** `web` | `engine` (FastAPI, no trade loop) | `trader` (same image, lease owner) share Postgres DB `trader` on the existing Coolify standalone Postgres. JSON `paper_state.json` migrates once. Cursor talks to `/control/*` via MCP.

**Tech Stack:** FastAPI, asyncpg/SQLAlchemy or psycopg, Alembic (or simple SQL migrations), Docker Compose / Coolify, Next.js proxy, MCP TypeScript server, Bearer `TRADER_CONTROL_TOKEN`.

**Spec:** `docs/superpowers/specs/2026-11-05-zero-downtime-trader-design.md`

## Global Constraints

- Paper mode only (`mode=paper`); no live exchange write keys.
- Fail-closed: no fills if lease ambiguous or DB unreachable.
- Secrets only in Coolify / Cursor MCP env — never git.
- Use existing Coolify Postgres `pb12t523fbppov71muhtlaz3` — never `coolify-db`.
- Preserve open paper positions during cutover (migrate `engine_data` JSON).
- Trader UUID Coolify: `onx2blngujkokmltpugysuh4` / https://trader.eludein.art

## File map

| Path | Role |
|------|------|
| `apps/engine/app/db.py` | Engine + connection pool |
| `apps/engine/app/migrations/*.sql` | Schema versions |
| `apps/engine/app/store.py` | Load/save account, positions, journal, params, audit |
| `apps/engine/app/lease.py` | Acquire / heartbeat / release |
| `apps/engine/app/persist.py` | Keep as JSON backup + one-shot importer |
| `apps/engine/app/runtime.py` | Persist via `store` instead of JSON-only; reload params between ticks |
| `apps/engine/app/trader_main.py` | Worker entrypoint |
| `apps/engine/main.py` | API-only lifespan; `/control/*` routes |
| `apps/engine/Dockerfile` | unchanged base; CMD overridden in compose for trader |
| `docker-compose.yml` | `web`, `engine`, `trader`, `DATABASE_URL` |
| `apps/mcp-trader/` | MCP server wrapping control API |
| `.env.example` | `DATABASE_URL`, `TRADER_CONTROL_TOKEN` |

---

### Task 1: Postgres provisioning + schema migrations

**Files:**
- Create: `apps/engine/app/db.py`
- Create: `apps/engine/app/migrations/001_init.sql`
- Create: `apps/engine/app/migrate.py`
- Modify: `apps/engine/requirements.txt` (add `psycopg[binary]>=3.2`)
- Modify: `.env.example`

**Interfaces:**
- Produces: `get_conn()`, `run_migrations()`, tables listed in spec

- [ ] **Step 1: Add dependency**

Append to `apps/engine/requirements.txt`:

```
psycopg[binary]>=3.2
```

- [ ] **Step 2: Write `001_init.sql`**

Create `apps/engine/app/migrations/001_init.sql` with:

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
  id TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bot_lease (
  id INT PRIMARY KEY CHECK (id = 1),
  owner_id TEXT,
  heartbeat_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  release_requested BOOLEAN NOT NULL DEFAULT false,
  version BIGINT NOT NULL DEFAULT 0
);
INSERT INTO bot_lease (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS account (
  id INT PRIMARY KEY CHECK (id = 1),
  mode TEXT NOT NULL DEFAULT 'paper',
  cash DOUBLE PRECISION NOT NULL,
  initial_balance DOUBLE PRECISION NOT NULL,
  realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
  peak_equity DOUBLE PRECISION NOT NULL,
  killed BOOLEAN NOT NULL DEFAULT false,
  kill_reason TEXT,
  session_started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS positions (
  symbol TEXT PRIMARY KEY,
  qty DOUBLE PRECISION NOT NULL,
  avg_entry DOUBLE PRECISION NOT NULL,
  stop_pct DOUBLE PRECISION,
  take_pct DOUBLE PRECISION,
  exit_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trades (
  id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  qty DOUBLE PRECISION NOT NULL,
  price DOUBLE PRECISION NOT NULL,
  fee DOUBLE PRECISION NOT NULL DEFAULT 0,
  ts TIMESTAMPTZ NOT NULL,
  realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
  note TEXT
);
CREATE INDEX IF NOT EXISTS trades_ts_idx ON trades (ts DESC);

CREATE TABLE IF NOT EXISTS journal (
  id TEXT PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL,
  payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS journal_ts_idx ON journal (ts DESC);

CREATE TABLE IF NOT EXISTS equity_points (
  ts TIMESTAMPTZ NOT NULL,
  equity DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (ts)
);

CREATE TABLE IF NOT EXISTS bot_params (
  id INT PRIMARY KEY CHECK (id = 1),
  params JSONB NOT NULL DEFAULT '{}'::jsonb,
  alerts JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO bot_params (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS runtime_meta (
  id INT PRIMARY KEY CHECK (id = 1),
  last_buy_at JSONB NOT NULL DEFAULT '{}'::jsonb,
  signal_history JSONB NOT NULL DEFAULT '[]'::jsonb,
  migrated_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO runtime_meta (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS control_audit (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
```

- [ ] **Step 3: Implement `db.py` + `migrate.py`**

`apps/engine/app/db.py`:

```python
from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def require_database_url() -> str:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required")
    return DATABASE_URL


@contextmanager
def get_conn():
    with psycopg.connect(require_database_url(), autocommit=False) as conn:
        yield conn
```

`apps/engine/app/migrate.py`: run all `app/migrations/*.sql` not in `schema_migrations`.

- [ ] **Step 4: Provision DB on VPS (manual once)**

On Coolify Postgres container `pb12t523fbppov71muhtlaz3`:

```bash
# create role + db trader (password via Coolify UI / secrets — do not commit)
docker exec -i pb12t523fbppov71muhtlaz3 psql -U postgres -c "CREATE USER trader WITH PASSWORD '...';"
docker exec -i pb12t523fbppov71muhtlaz3 psql -U postgres -c "CREATE DATABASE trader OWNER trader;"
```

Set Coolify app env `DATABASE_URL=postgresql://trader:...@pb12t523fbppov71muhtlaz3:5432/trader` (internal Docker network name as Coolify exposes it).

- [ ] **Step 5: Local migrate smoke**

```bash
cd apps/engine
set DATABASE_URL=postgresql://...
python -m app.migrate
```

Expected: `001_init` applied, tables exist.

- [ ] **Step 6: Commit**

```bash
git add apps/engine/app/db.py apps/engine/app/migrate.py apps/engine/app/migrations/001_init.sql apps/engine/requirements.txt .env.example
git commit -m "feat(engine): add Postgres schema migrations for durable paper state"
```

---

### Task 2: Store layer + JSON importer

**Files:**
- Create: `apps/engine/app/store.py`
- Modify: `apps/engine/app/persist.py` (add `import_json_to_store` helper using existing `load_state`)
- Test: `apps/engine/tests/test_store_roundtrip.py`

**Interfaces:**
- Consumes: `get_conn`, schema from Task 1
- Produces: `Store.load_runtime()`, `Store.save_runtime(runtime)`, `Store.maybe_migrate_json()`, `Store.save_params`, `Store.load_params`, `Store.audit`

- [ ] **Step 1: Write failing roundtrip test** (wallet cash + one position + exit levels)

- [ ] **Step 2: Implement `Store`** mapping `TradingRuntime` fields including `_exit_levels` into `positions.exit_meta` / `stop_pct` / `take_pct`

- [ ] **Step 3: Implement one-shot migrate** — if `account` missing and `paper_state.json` exists, import in one transaction, set `runtime_meta.migrated_at`

- [ ] **Step 4: Run tests**

```bash
cd apps/engine && python -m pytest tests/test_store_roundtrip.py -v
```

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(engine): Postgres store and JSON paper_state importer"
```

---

### Task 3: Lease protocol

**Files:**
- Create: `apps/engine/app/lease.py`
- Test: `apps/engine/tests/test_lease.py`

**Interfaces:**
- Produces: `LeaseManager.acquire(owner_id, ttl_sec=20) -> bool`, `heartbeat()`, `request_release()`, `release()`, `is_owner() -> bool`

- [ ] **Step 1: Failing tests** for acquire exclusivity, expiry steal, release_requested

- [ ] **Step 2: Implement with SQL**

```sql
UPDATE bot_lease SET owner_id=%s, heartbeat_at=now(), expires_at=now()+(%s||' seconds')::interval,
  release_requested=false, version=version+1
WHERE id=1 AND (owner_id IS NULL OR expires_at < now() OR owner_id=%s)
RETURNING version;
```

- [ ] **Step 3: Tests pass + commit**

```bash
git commit -m "feat(engine): bot lease acquire/heartbeat for single trader owner"
```

---

### Task 4: Split engine API vs trader worker

**Files:**
- Create: `apps/engine/app/trader_main.py`
- Modify: `apps/engine/main.py` (remove `runtime.start()` from lifespan; keep API)
- Modify: `apps/engine/app/runtime.py` (persist via Store; check lease each tick; reload params between ticks; persist `_exit_levels`)
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: Store, LeaseManager
- Produces: trader process that acquires lease then `await runtime.start()`

- [ ] **Step 1: `trader_main.py`**

```python
async def main() -> None:
    run_migrations()
    store.maybe_migrate_json()
    owner = f"trader-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    lease = LeaseManager(owner)
    if not lease.acquire():
        raise SystemExit("could not acquire lease")
    runtime = TradingRuntime(store=store, lease=lease)
    await runtime.start()
    # loop until lease lost / SIGTERM → runtime.stop(); lease.release()
```

- [ ] **Step 2: API lifespan** only constructs runtime for read/write via Store (or shared read models) **without** starting loops

- [ ] **Step 3: Compose**

```yaml
  engine:
    # existing build
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - TRADER_CONTROL_TOKEN=${TRADER_CONTROL_TOKEN:-}
    # no trade loop

  trader:
    build:
      context: ./apps/engine
      dockerfile: Dockerfile
    command: ["python", "-m", "app.trader_main"]
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - APP_ENV=production
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
    volumes:
      - engine_data:/app/data
    restart: unless-stopped
    depends_on:
      - engine
```

- [ ] **Step 4: Local compose up — verify only one lease owner; API `/health` includes db+lease**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: split trader worker from engine API with lease-gated loops"
```

---

### Task 5: Control API + audit

**Files:**
- Create: `apps/engine/app/control_auth.py`
- Modify: `apps/engine/main.py` (routes `/control/status|params|kill|resume|tick`)
- Test: `apps/engine/tests/test_control_api.py`

**Interfaces:**
- Produces: Bearer auth dependency; mutating routes write `bot_params` / `account` + `control_audit`

- [ ] **Step 1: Auth dependency** — compare `Authorization: Bearer` to `TRADER_CONTROL_TOKEN`; 401 if missing/mismatch; refuse mutate if token unset in production

- [ ] **Step 2: Routes** as in spec; params validated with existing `BotParams`

- [ ] **Step 3: Tests with TestClient + temp DB or mocked Store**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(engine): token-secured /control API for Cursor strategy updates"
```

---

### Task 6: Web proxy for `/api/engine/control/*`

**Files:**
- Modify: existing web engine proxy under `apps/web/app/api/engine/` (mirror path)

- [ ] **Step 1: Ensure proxy forwards Authorization header** to engine

- [ ] **Step 2: Manual curl**

```bash
curl -s -H "Authorization: Bearer $TRADER_CONTROL_TOKEN" https://trader.eludein.art/api/engine/control/status
```

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(web): proxy control API headers to engine"
```

---

### Task 7: MCP `trader` server

**Files:**
- Create: `apps/mcp-trader/package.json`
- Create: `apps/mcp-trader/src/index.ts`
- Create: `apps/mcp-trader/README.md` (Cursor MCP config snippet)

**Interfaces:**
- Tools: `get_status`, `get_params`, `set_params`, `kill`, `resume`, `tick`
- Env: `TRADER_BASE_URL`, `TRADER_CONTROL_TOKEN`

- [ ] **Step 1: Scaffold MCP server** calling HTTPS control routes

- [ ] **Step 2: Document Cursor `mcp.json` entry** (token via env, not file in repo)

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: add MCP trader client for Cursor control plane"
```

---

### Task 8: Prod cutover (Coolify)

**Files:** none in repo (ops checklist)

- [ ] **Step 1: Snapshot** copy `paper_state.json` from volume `onx2blngujkokmltpugysuh4_engine-data` off-box

- [ ] **Step 2: Set** `DATABASE_URL` + `TRADER_CONTROL_TOKEN` in Coolify (UI or SQL env — avoid empty encrypted stubs)

- [ ] **Step 3: Deploy** compose with `trader` service; watch lease heartbeat via `/control/status`

- [ ] **Step 4: Verify** cash/positions match pre-deploy JSON; journal continuity

- [ ] **Step 5: Smoke** web UI + MCP `get_status` / `set_params` no-op tweak

- [ ] **Step 6: Document** in README deploy notes (redeploy web/engine freely; trader only on logic change)

---

## Spec coverage check

| Spec item | Task |
|-----------|------|
| web/engine/trader split | 4 |
| Postgres on existing Coolify PG | 1 |
| Lease handoff | 3–4 |
| Migrate paper_state.json | 2, 8 |
| Persist exit levels | 2, 4 |
| Control API + audit | 5–6 |
| MCP Cursor channel | 7 |
| Fail-closed / paper mode | 3–5 |
| Coolify cutover | 8 |

## Execution handoff

Plan saved to `docs/superpowers/plans/2026-11-05-zero-downtime-trader.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — same session with executing-plans checkpoints  

Which approach?
