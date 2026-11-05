# Task 3 report — Lease protocol

## Status
Done. `LeaseManager` implements acquire / heartbeat / request_release / release / is_owner with fail-closed exclusivity.

## Files
- `apps/engine/app/lease.py` — new
- `apps/engine/tests/test_lease.py` — new (4 tests)

## Tests
```
pytest tests/test_lease.py -v  → 4 passed (SQLite shim via `store_db`)
```

Coverage:
- Second owner cannot acquire while TTL valid
- Expired lease can be stolen
- `request_release` sets flag; holder remains owner until `release`
- `heartbeat` extends TTL for owner only

## Commit
Message: `feat(engine): bot lease acquire/heartbeat for single trader owner`

## Notes / concerns
- Acquire/heartbeat use Python `timedelta` + bound timestamps (not `now()+interval` SQL) so the SQLite test shim matches Postgres semantics.
- `request_release` requires an active `owner_id` (no-op when lease empty).
- Task 4 should wire `trader_main` + reset `bot_lease` in Postgres truncate fixture if integration tests share DB.

## Out of scope (Task 4)
- `trader_main.py`, runtime lease checks, compose `trader` service.
