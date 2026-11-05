# Task 4 report — Split engine API vs trader worker

## Status
Done. The engine process is an HTTP API with no trade loops. A separate `trader` process acquires `bot_lease`, reloads Postgres between ticks, and is the only process allowed to create paper fills.

## Commit
`a8fbb08` — `feat: split trader worker from engine API with lease-gated loops`

## Files
- `apps/engine/app/trader_main.py` — migrations, `maybe_migrate_json`, lease acquire, `TradingRuntime(store, lease)`, supervise until SIGTERM or lease loss, then `stop` / `shutdown_streams` / `stop_heartbeat` / `close` / `release`
- `apps/engine/main.py` — lifespan hydrates Store and does **not** call `runtime.start()`; `GET /health` includes `db` + `lease`; `POST /bot/tick` returns 409 without a lease
- `apps/engine/app/runtime.py` — Store persist (exit levels included), param reload between ticks, lease check each tick, 5s heartbeat, fail-closed on lease loss or DB write failure
- `apps/engine/app/lease.py` — `read_lease()`, `release_requested()`
- `apps/engine/app/store.py` — `set_kill()` updates kill flags without rewriting the book
- `apps/engine/tests/test_trader_split.py` — API cannot start loops or fill; params reload; lease confirm; exit levels round-trip
- `docker-compose.yml` — `DATABASE_URL` + `TRADER_CONTROL_TOKEN` on `engine`; `trader` service (`python -m app.trader_main`, `engine_data` volume)

## Behavior
- Only a runtime that holds the lease calls `wallet.buy` / `wallet.sell`. The API store-backed process raises `LeaseLost` instead.
- API kill/resume writes `account.killed` and `bot_params` (`auto_trade`). The trader flattens on its next tick after reload.
- Postgres is the source of truth. After a successful `save_runtime`, JSON is still written under `engine_data` as a backup. `maybe_migrate_json` remains the one-shot import when `account` is empty.
- Lost lease, `release_requested`, or a failed persist stops the trade loop and does not place further orders.
- `/health` shape: `status` (`ok` or `degraded`), `db.ok`, `lease` (`owner_id`, `heartbeat_at`, `expires_at`, `held`, `release_requested`, `version`), `trade_loops: false` on the API.
- `bot_running` on the API follows a non-expired lease owner, not an in-process loop.

## Tests / smoke
```text
cd apps/engine
python -m pytest tests/test_trader_split.py tests/test_lease.py tests/test_store_roundtrip.py -q
→ 12 passed

python -c "import app.trader_main; import main" → imports-ok
python -m app.trader_main with DATABASE_URL unset → RuntimeError: DATABASE_URL is required
```

Docker is not on PATH here, so `docker compose config` and a live "one lease owner" check were not run.

## Concerns
1. No local Docker or Postgres. Lease exclusivity is covered by the SQLite shim tests, not a compose stack.
2. API `reset_account` still `save_runtime`s the whole book. A tick already in flight can write the pre-reset book back. Kill uses `set_kill` and does not rewrite positions.
3. Heartbeat every 5s (TTL 20s) keeps the lease during long ticks. Compose `restart` plus a 5s backoff on failed acquire avoids a tight crash loop while the other owner is alive.
4. Trader env also passes SMTP (same as engine) so fill alerts still send. The brief listed `TELEGRAM_BOT_TOKEN` only.
5. Both processes run `run_migrations` and `maybe_migrate_json`. The import is a no-op once `account` exists.
6. `POST /bot/start` and `/bot/stop` on the API do not start or stop the worker. Start the `trader` service; stop trading with kill / `auto_trade` or by releasing the lease.

## Brief checklist
- [x] `trader_main.py` acquire → `runtime.start()` → stop and `lease.release()` on SIGTERM or lease loss
- [x] API lifespan does not start loops
- [x] Runtime persists via Store, including `_exit_levels`; reloads params between ticks; checks lease each tick
- [x] Compose `trader` service + `DATABASE_URL`
- [ ] Local compose up / single lease owner (docker unavailable)
- [x] Commit message per brief

## Follow-up — review findings 1 and 2

### Commit
`749fd3e` — `fix: keep account reset and lease release on the owner`

### Reset (finding 1)
`POST /account/reset` on the API does not call `wallet.reset` or `save_runtime`. It upserts `runtime_meta.reset_requested` and optional `reset_balance` (`Store.request_reset`) and returns the current account with `reset_accepted: true` and `reset_requested: true` (HTTP 200 so the dashboard refresh still works). Only the lease owner applies the flag: `TradingRuntime._apply_pending_reset` on start and between ticks runs the wallet reset, `save_runtime`, then `clear_reset_request`. `save_runtime` does not touch those columns, so an in-flight tick can write the pre-reset book back and the next loop still applies the reset. A process with no store (legacy JSON) still resets locally. A store-backed runtime whose lease check fails also only sets the flag.

Migration: `apps/engine/app/migrations/002_reset_requested.sql`.

### Shutdown (finding 2)
`stop()` no longer cancels the loop task. Cancelling it does not join `asyncio.to_thread`, and the lock drops while that thread can still persist. `stop()` sets `running` false and awaits the loop task so the in-flight tick finishes first. `trader_main` still calls `stop()` before `lease.release()`. After release, `_persist_state` raises `LeaseLost` when `lease` is set and `is_owner()` is false, so a late writer cannot replace positions or trades. The loop may spend up to one second in its sleep chunk after the tick before `stop()` returns; that wait is still before release.

### Tests
```text
cd apps/engine
python -m pytest tests/test_trader_split.py tests/test_lease.py tests/test_store_roundtrip.py -q
→ 15 passed
```
Added `test_api_reset_flags_book_until_trader_applies`, `test_stop_waits_for_inflight_tick`, and `test_persist_after_release_does_not_save`.

Concern 2 above is closed for reset. Kill/resume still use `set_kill` and do not rewrite positions when the account row exists.
