# Execution twin (shadow) — design

Date: 2026-11-06  
Status: implemented (phase 1)

## Goal

Avoid a prohibitive paper→live gap. Paper fills must be **at least as hard as** a Coinbase marketable taker order: real L2 walk + real fee tier + latency re-quote. No real orders in phase 1.

## Behavior

1. On buy/sell, wait `latency_ms`, then `GET /products/{id}/book?level=2`.
2. Walk asks (buy) or bids (sell) for VWAP fill; reject if filled notional &lt; `twin_min_fill_notional`.
3. Wallet fees use `taker_fee_bps` (`fee_bps` alias). Defaults: `fee_tier=us_intro` (90/50).
4. `GET /fidelity` exposes sample counts and `go_live_ready` (false until ≥100 twin fills and ≥14 days, friction ≥ 2× taker, unfilled rate acceptable).

## Out of scope

Private API keys, live order placement, WebSocket L2, historical L2 backtest.

## Files

- `app/market/coinbase.py` — `get_order_book`
- `app/fill_model.py` — `walk_real_book`
- `app/fees.py` / `app/fidelity.py` / `app/config.py` / `app/runtime.py`
- `GET /fidelity`, stubs `execution_twin`
