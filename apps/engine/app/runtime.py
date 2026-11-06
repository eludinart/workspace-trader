from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.alerts import AlertService
from app.config import AlertParams, BotParams
from app.events import EventBus
from app.lease import read_lease
from app.execution import OrderPlan, buy_spend, cooldown_active, plan_protective, plan_signal
from app.fidelity import FillSample, FidelityTracker
from app.fill_model import impact_bps_for_notional, quote_fill, walk_book_fill, walk_real_book
from app.journal import DecisionJournal
from app.market.coinbase import CoinbaseClient
from app.narrative import decision_narrative, format_other_exits, thresholds_from_detail
from app.news.service import NewsService
from app.equity_history import EquityHistory
from app.persist import STATE_PATH, clear_state, load_state, parse_last_buys, save_state
from app.risk import (
    annotate_levels,
    compute_exit_levels,
    dynamic_max_open,
    net_pnl_if_closed,
    portfolio_open_risk_r,
    risk_position_spend,
    sell_is_net_positive,
)
from app.signals.flags import SignalFeatureFlags, env_flag_overrides
from app.signals.gates import evaluate_entry_gates
from app.signals.kelly import evaluate_kelly
from app.signals.oos import evaluate_oos
from app.signals.passive_wake import evaluate_passive_wake
from app.signals.post_trade import run_post_trade_review
from app.signals.risk_caps import HARD_RISK_PER_TRADE_PCT, clamp_risk_per_trade_pct
from app.strategy.features import build_feature_frame
from app.strategy.xgboost_strategy import SignalResult, XGBoostStrategy
from app.trading_style import apply_style_preset, normalize_style, scan_gates
from app.universe import UniverseService
from app.wallet import PaperWallet, Trade, annotate_trade_for_display

if TYPE_CHECKING:
    from app.lease import LeaseManager
    from app.store import Store

logger = logging.getLogger(__name__)


class LeaseLost(RuntimeError):
    """Lease missing, stolen, or the database could not confirm ownership."""


class PersistenceError(RuntimeError):
    """Postgres write failed. Trading must stop rather than continue from memory."""

_NOTE_REASONS = {
    "exit:tp": "tp",
    "exit:sl": "sl",
    "exit:kill": "kill",
    "exit:manual": "manual",
    "entry:signal": "signal",
    "exit:signal": "signal",
    "hold:budget": "budget",
    "hold:cooldown": "cooldown",
    "hold:flat": "flat",
    "hold:max_size": "max_size",
    "hold:max_symbols": "max_symbols",
    "hold:fee_guard": "fee_guard",
    "hold:min_hold": "min_hold",
    "hold:portfolio_risk": "portfolio_risk",
}


def _plan_from_note(side: str, note: str) -> OrderPlan:
    reason = _NOTE_REASONS.get(note, "signal")
    fraction = 1.0 if side == "sell" else 0.0
    return OrderPlan(side, fraction, reason, note)



def _utc_now_safe() -> datetime:
    return datetime.now(timezone.utc)


class TradingRuntime:
    def __init__(self, store: Store | None = None, lease: LeaseManager | None = None) -> None:
        self.store = store
        self.lease = lease
        self._db_ok = store is None
        self.params = BotParams()
        self.wallet = PaperWallet(initial_balance=self.params.initial_balance)
        self.market = CoinbaseClient()
        self.strategy = XGBoostStrategy()
        self.news = NewsService()
        self.alerts = AlertService()
        self.journal = DecisionJournal()
        self.bus = EventBus()
        self.running = False
        self.stream_alive = False
        self.killed = False
        self.kill_reason: str | None = None
        self.peak_equity: float = self.params.initial_balance
        self.last_price: float | None = None
        self.last_signal: SignalResult | None = None
        self.last_tick_at: datetime | None = None
        self.last_error: str | None = None
        self.signal_history: list[dict[str, Any]] = []
        self._task: asyncio.Task[None] | None = None
        self._price_task: asyncio.Task[None] | None = None
        self._news_task: asyncio.Task[None] | None = None
        self._lease_task: asyncio.Task[None] | None = None
        self._heartbeat_alive = False
        self._loop_stopped = asyncio.Event()
        self._loop_stopped.set()
        self._lock = asyncio.Lock()
        self.active_symbol: str = self.params.symbol
        self.last_signal_symbol: str | None = None
        self.prices: dict[str, float] = {}
        self.quotes: dict[str, dict[str, float]] = {}
        self.fidelity = FidelityTracker()
        self._rr_index = 0
        self._price_rr = 0
        self._last_buy_at: dict[str, datetime] = {}
        # Per-symbol ATR exit levels locked at entry (stop / take as fractions).
        self._exit_levels: dict[str, dict[str, Any]] = {}
        self.universe = UniverseService(
            self.market,
            size=int(self.params.universe_size),
            min_volume_usd=float(self.params.universe_min_volume_usd),
            refresh_hours=float(self.params.universe_refresh_hours),
            core=["BTC-USD", "ETH-USD", "SOL-USD"],
        )
        # Last known signal snapshot per universe symbol (for UI table).
        self._universe_signals: dict[str, dict[str, Any]] = {}
        self.equity_history = EquityHistory()
        self._last_equity_persist_mono: float = 0.0
        if store is None:
            self._restore_persisted_state()

    def _restore_persisted_state(self) -> None:
        data = load_state()
        if not data:
            return
        try:
            wallet_data = data.get("wallet")
            if isinstance(wallet_data, dict):
                self.wallet.restore_from_dict(wallet_data)
            if data.get("peak_equity") is not None:
                self.peak_equity = float(data["peak_equity"])
            else:
                self.peak_equity = max(self.peak_equity, self.wallet.cash)
            journal_rows = data.get("journal")
            if isinstance(journal_rows, list):
                self.journal.load_dicts(journal_rows)
            history = data.get("signal_history")
            if isinstance(history, list):
                self.signal_history = [row for row in history if isinstance(row, dict)][-100:]
            self._last_buy_at = parse_last_buys(data.get("last_buy_at"))
            if data.get("killed"):
                self.killed = True
                self.kill_reason = str(data.get("kill_reason") or "restored")
            hist = data.get("equity_history")
            if isinstance(hist, list):
                self.equity_history.restore(
                    hist,
                    session_started_at=str(data.get("session_started_at") or "") or None,
                )
            logger.info(
                "restored paper state: cash=%.2f positions=%d trades=%d",
                self.wallet.cash,
                len(self.wallet.positions),
                len(self.wallet.trades),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("persist restore failed: %s", exc)

    def _state_payload(self) -> dict[str, Any]:
        return {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "peak_equity": self.peak_equity,
            "killed": self.killed,
            "kill_reason": self.kill_reason,
            "wallet": {
                "initial_balance": self.wallet.initial_balance,
                "cash": self.wallet.cash,
                "realized_pnl": self.wallet.realized_pnl,
                "positions": [
                    {"symbol": p.symbol, "qty": p.qty, "avg_entry": p.avg_entry}
                    for p in self.wallet.positions.values()
                ],
                "trades": [annotate_trade_for_display(t) for t in self.wallet.trades[-200:]],
            },
            "journal": [e.to_dict() for e in self.journal.entries[-200:]],
            "signal_history": self.signal_history[-50:],
            "last_buy_at": {k: v.isoformat() for k, v in self._last_buy_at.items()},
            "session_started_at": self.equity_history.session_started_at.isoformat(),
            "equity_history": self.equity_history.dump()[-4000:],
            "exit_levels": self._exit_levels,
        }

    def _persist_state(self) -> None:
        if self.lease is not None:
            try:
                owned = self.lease.is_owner()
            except Exception as exc:  # noqa: BLE001
                self._db_ok = False
                raise LeaseLost("persist refused: lease check failed") from exc
            if not owned:
                raise LeaseLost("persist refused: lease not held")
        payload = self._state_payload()
        if self.store is not None:
            try:
                self.store.save_runtime(self)
            except Exception as exc:  # noqa: BLE001
                self._db_ok = False
                raise PersistenceError("database persist failed") from exc
            self._db_ok = True
            save_state(payload)
            return
        save_state(payload)

    def _save_params(self) -> None:
        if self.store is None:
            return
        self.store.save_params(self.params, self.alerts.params)
        self._db_ok = True

    def _apply_env_signal_flags(self, params: BotParams) -> BotParams:
        """Overlay Coolify SIGNAL_* / WEB_PUSH_ENABLED when explicitly set."""
        overrides = env_flag_overrides(os.getenv)
        if not overrides:
            return params
        data = params.model_dump()
        data.update(overrides)
        return BotParams(**data)

    def _reload_params_only(self) -> None:
        if self.store is None:
            return
        params, alerts = self.store.load_params()
        self.params = self._apply_env_signal_flags(params)
        self.alerts.params = alerts
        self._db_ok = True

    def _reload_between_ticks(self) -> None:
        """Postgres is the source of truth between decisions."""
        if self.store is None:
            return
        self.store.load_runtime(self)
        self._reload_params_only()
        if self.params.universe_enabled:
            self.universe.size = int(self.params.universe_size)
            self.universe.min_volume_usd = float(self.params.universe_min_volume_usd)
            self.universe.refresh_hours = float(self.params.universe_refresh_hours)
        self._db_ok = True

    def hydrate(self) -> bool:
        if self.store is None:
            return False
        try:
            self._reload_between_ticks()
        except Exception:
            self._db_ok = False
            raise
        return True

    def _confirm_lease(self, *, respect_release: bool = False) -> bool:
        if self.lease is None:
            return False
        try:
            if not self.lease.heartbeat():
                return False
            if respect_release and self.lease.release_requested():
                logger.info("lease release requested for %s", self.lease.owner_id)
                return False
            return self.lease.is_owner()
        except Exception:  # noqa: BLE001
            logger.exception("lease check failed")
            self._db_ok = False
            return False

    def _require_fill_authority(self) -> None:
        """Only the live lease owner may create paper fills."""
        if self.lease is None:
            if self.store is not None:
                raise LeaseLost("fills are only allowed in the trader process")
            return
        if not self._confirm_lease(respect_release=False):
            raise LeaseLost("lease lost or database unavailable")

    def close(self) -> None:
        should_persist = False
        if self.lease is not None:
            try:
                should_persist = self.lease.is_owner()
            except Exception as exc:  # noqa: BLE001
                logger.warning("lease check on close: %s", exc)
                should_persist = False
        elif self.store is None:
            should_persist = True
        if should_persist:
            try:
                self._persist_state()
            except Exception as exc:  # noqa: BLE001
                logger.warning("persist on close: %s", exc)
        self.market.close()

    async def start(self) -> None:
        if self.running:
            return
        if self.lease is None:
            logger.info("trade loops stay stopped (API process has no lease)")
            return
        if not self._confirm_lease(respect_release=True):
            raise LeaseLost("cannot start trading loops without a live lease")
        await asyncio.to_thread(self._apply_pending_reset)
        self.running = True
        self.stream_alive = True
        self._heartbeat_alive = True
        self._loop_stopped.clear()
        try:
            await asyncio.to_thread(self._ensure_universe, True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("universe on start: %s", exc)
        self._task = asyncio.create_task(self._loop())
        if self._lease_task is None or self._lease_task.done():
            self._lease_task = asyncio.create_task(self._lease_heartbeat_loop())
        if self._price_task is None or self._price_task.done():
            self._price_task = asyncio.create_task(self._price_loop())
        if self._news_task is None or self._news_task.done():
            self._news_task = asyncio.create_task(self._news_loop())

    async def _lease_heartbeat_loop(self) -> None:
        while self._heartbeat_alive:
            if self.lease is None:
                return
            try:
                if not self.lease.heartbeat() or not self.lease.is_owner():
                    logger.error("lease lost during heartbeat")
                    self.running = False
                    self._db_ok = False
            except Exception:  # noqa: BLE001
                logger.exception("lease heartbeat failed; fail-closed")
                self.running = False
                self._db_ok = False
            await asyncio.sleep(5)

    async def wait_loop(self) -> None:
        await self._loop_stopped.wait()

    async def stop_heartbeat(self) -> None:
        self._heartbeat_alive = False
        task = self._lease_task
        self._lease_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _news_loop(self) -> None:
        while self.stream_alive:
            try:
                await self._refresh_news_and_publish()
            except Exception as exc:  # noqa: BLE001
                logger.warning("news loop: %s", exc)
            await asyncio.sleep(120)

    def _ensure_universe(self, force: bool = False) -> None:
        if not self.params.universe_enabled:
            return
        self.universe.size = int(self.params.universe_size)
        self.universe.min_volume_usd = float(self.params.universe_min_volume_usd)
        self.universe.refresh_hours = float(self.params.universe_refresh_hours)
        symbols = self.universe.refresh(force=force)
        # Merge news mentions into the book.
        brief = self.news.cached_brief()
        if brief is not None:
            headlines = [h.to_dict() for h in brief.headlines]
            self.universe.ingest_news(headlines)
            symbols = list(self.universe.symbols)
        if symbols:
            self.params.symbols = symbols

    def _effective_max_open(self, equity: float | None = None) -> int:
        eq = float(equity if equity is not None else self._equity())
        dyn = dynamic_max_open(
            eq,
            capital_per_slot=float(self.params.capital_per_slot),
            hard_cap=int(self.params.max_open_hard_cap),
            floor=5,
        )
        return int(min(int(self.params.max_open_symbols), dyn) if self.params.max_open_symbols else dyn)

    def universe_view(self) -> dict[str, Any]:
        self._ensure_universe(force=False)
        snap = self.universe.snapshot(self._universe_signals)
        eq = self._equity()
        snap["max_open"] = self._effective_max_open(eq)
        snap["equity"] = eq
        snap["open_positions"] = list(self.wallet.positions.keys())
        return snap

    async def stop(self) -> None:
        """Stop after the in-flight tick persists. Do not cancel that tick.

        ``asyncio.to_thread`` keeps running if the loop task is cancelled, and
        the lock is released while the thread can still call ``_persist_state``.
        Callers must release the lease only after this returns.
        """
        self.running = False
        task = self._task
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("trade loop failed while stopping")
        self._task = None

    async def shutdown_streams(self) -> None:
        self.stream_alive = False
        if self._price_task:
            self._price_task.cancel()
            try:
                await self._price_task
            except asyncio.CancelledError:
                pass
            self._price_task = None
        if self._news_task and not self._news_task.done():
            self._news_task.cancel()
            try:
                await self._news_task
            except asyncio.CancelledError:
                pass
            self._news_task = None

    async def _refresh_news_and_publish(self) -> None:
        try:
            refreshed = await asyncio.to_thread(self.news.refresh_if_stale)
            brief = self.news.cached_brief()
            if brief is not None:
                headlines = [h.to_dict() for h in brief.headlines]
                added = self.universe.ingest_news(headlines)
                if added and self.params.universe_enabled:
                    self.params.symbols = list(self.universe.symbols)
            if refreshed or True:
                await self.publish_live("news")
        except Exception as exc:  # noqa: BLE001
            logger.warning("news refresh: %s", exc)

    async def _price_loop(self) -> None:
        while self.stream_alive:
            try:
                await asyncio.to_thread(self._refresh_price_only)
                closed: list[dict[str, Any]] = []
                try:
                    async with self._lock:
                        closed = await asyncio.to_thread(self._apply_pending_closes)
                except (LeaseLost, PersistenceError) as exc:
                    logger.error("pending close failed: %s", exc)
                recorded = self._record_equity_sample()
                if recorded:
                    await asyncio.to_thread(self._maybe_persist_equity_ring)
                await self.publish_live("close" if closed else "price")
            except Exception as exc:  # noqa: BLE001
                logger.warning("price loop: %s", exc)
            await asyncio.sleep(2)

    def _record_equity_sample(self, *, force: bool = False) -> bool:
        try:
            eq = self._equity()
            self.peak_equity = max(self.peak_equity, eq)
            return self.equity_history.maybe_record(
                equity=eq,
                cash=float(self.wallet.cash),
                positions=len(self.wallet.positions),
                pnl=eq - float(self.wallet.initial_balance),
                peak_equity=float(self.peak_equity),
                force=force,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("equity sample skipped: %s", exc)
            return False

    def _maybe_persist_equity_ring(self) -> None:
        """Push dense equity samples to Postgres so the API chart stays full."""
        if self.store is None or self.lease is None:
            return
        try:
            if not self._confirm_lease(respect_release=False):
                return
        except Exception:  # noqa: BLE001
            return

        now = time.monotonic()
        if self._last_equity_persist_mono and now - self._last_equity_persist_mono < 20.0:
            return
        try:
            self.store.save_equity_only(self)
            self._last_equity_persist_mono = now
            self._db_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("equity ring persist failed: %s", exc)

    def equity_curve(self, range_key: str = "1h") -> dict[str, Any]:
        # API process (store, no lease): read-only from hydrated Postgres.
        # Sampling belongs to the trader price loop — recording here polluted
        # the ring with cash/equity glitches (empty marks / half-hydrated wallet).
        if self.lease is None and self.store is not None:
            return self.equity_history.query(range_key)
        if not self.equity_history.dump():
            self._record_equity_sample(force=True)
        else:
            self._record_equity_sample(force=False)
        return self.equity_history.query(range_key)

    def _refresh_price_only(self) -> None:
        # Always mark open positions so portfolio equity moves live.
        try:
            open_syms = list(self.wallet.positions.keys())
        except RuntimeError:
            open_syms = []
        for sym in open_syms:
            self._cache_ticker(sym)
        book = self.watchlist()
        if not book:
            return
        focus = book[self._price_rr % len(book)]
        self._price_rr += 1
        for sym in self._quote_symbols(focus):
            self._cache_ticker(sym)
        primary = self.prices.get(self.params.symbol) or self.prices.get(focus)
        if primary is not None:
            self.last_price = primary

    def decision_feed(self, limit: int = 12) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for entry in reversed(self.signal_history):
            text = str(entry.get("narrative") or "")
            if not text:
                continue
            decision = entry.get("decision") or {}
            signal = entry.get("signal") or {}
            rows.append(
                {
                    "ts": entry.get("ts"),
                    "symbol": entry.get("symbol"),
                    "action": decision.get("action") or signal.get("action"),
                    "reason": decision.get("reason"),
                    "confidence": signal.get("confidence"),
                    "order_placed": decision.get("order_placed"),
                    "narrative": text,
                }
            )
            if len(rows) >= limit:
                break
        return rows

    def signal_view(self) -> dict[str, Any]:
        latest = self.last_signal.to_dict() if self.last_signal else None
        explanation = self.strategy.explain(self.last_signal) if self.last_signal else None
        decisions = self.decision_feed()
        return {
            "latest": latest,
            "symbol": self.last_signal_symbol,
            "active_symbol": self.active_symbol,
            "explanation": explanation,
            "narrative": decisions[0]["narrative"] if decisions else None,
            "decisions": decisions,
            "history": list(reversed(self.signal_history[-20:])),
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
        }

    def live_payload(self, kind: str = "snapshot") -> dict[str, Any]:
        account = self.account_state()
        return {
            "type": kind,
            "ts": datetime.now(timezone.utc).isoformat(),
            "account": account,
            "price": self.last_price,
            "signal": self.signal_view(),
            "decisions": self.decision_feed(),
            "trades": [annotate_trade_for_display(t) for t in reversed(self.wallet.trades[-30:])],
            "journal": self.journal.list(limit=40, source="live"),
            "news": self.news.brief_dict(),
            "universe": {
                "symbols": list(self.universe.symbols),
                "size": len(self.universe.symbols),
                "news_hits": list(self.universe.news_hits)[:12],
                "updated_at": self.universe.updated_at.isoformat()
                if self.universe.updated_at
                else None,
            },
            "status": self.status(),
        }

    async def publish_live(self, kind: str = "snapshot") -> None:
        await self.bus.publish(self.live_payload(kind))

    def engage_kill_switch(self, reason: str = "manual") -> dict[str, Any]:
        if self.lease is None:
            self._reload_params_only()
        first = not self.killed
        self.killed = True
        self.kill_reason = reason
        self.params.auto_trade = False
        owns_fills = self.lease is not None and self._confirm_lease(respect_release=False)
        if owns_fills or self.store is None:
            self._flatten_all()
            if first:
                self.alerts.notify("Kill switch", f"Trading halted: {reason}", kind="kill")
            self._save_params()
            self._persist_state()
            return self.status()
        self._save_params()
        if self.store is not None and not self.store.set_kill(True, reason):
            self._persist_state()
        return self.status()

    def resume_from_kill(self) -> dict[str, Any]:
        self.killed = False
        self.kill_reason = None
        if self.store is not None:
            if not self.store.set_kill(False, None):
                self._persist_state()
        else:
            self._persist_state()
        return self.status()

    async def _sleep_interval(self) -> None:
        remaining = float(self.params.decision_interval_sec)
        while remaining > 0 and self.running:
            step = min(1.0, remaining)
            await asyncio.sleep(step)
            remaining -= step

    async def _loop(self) -> None:
        try:
            while self.running:
                if not self._confirm_lease(respect_release=True):
                    self.last_error = "lease lost"
                    logger.error("stopping trade loop: lease lost or database unavailable")
                    self.running = False
                    break
                try:
                    await asyncio.to_thread(self._reload_between_ticks)
                except Exception as exc:  # noqa: BLE001
                    self.last_error = f"store reload failed: {exc}"
                    logger.exception("store reload failed; fail-closed")
                    self.running = False
                    break
                try:
                    await asyncio.to_thread(self._apply_pending_reset)
                except (LeaseLost, PersistenceError) as exc:
                    self.last_error = str(exc)
                    logger.error("fail-closed: %s", exc)
                    self.running = False
                    break
                try:
                    await asyncio.to_thread(self._apply_pending_closes)
                except (LeaseLost, PersistenceError) as exc:
                    self.last_error = str(exc)
                    logger.error("fail-closed: %s", exc)
                    self.running = False
                    break
                try:
                    await self.tick()
                    self.last_error = None
                except (LeaseLost, PersistenceError) as exc:
                    self.last_error = str(exc)
                    logger.error("fail-closed: %s", exc)
                    self.running = False
                    break
                except Exception as exc:  # noqa: BLE001
                    self.last_error = str(exc)
                    logger.exception("tick failed")
                    try:
                        self._persist_state()
                    except (LeaseLost, PersistenceError) as persist_exc:
                        self.last_error = str(persist_exc)
                        logger.error("fail-closed: %s", persist_exc)
                        self.running = False
                        break
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        await self.publish_live("error")
                    except Exception:  # noqa: BLE001
                        pass
                await self._sleep_interval()
        finally:
            self.running = False
            self._loop_stopped.set()

    async def tick(self) -> dict[str, Any]:
        if self.lease is None:
            if self.store is not None:
                raise LeaseLost("tick refused: only the trader worker may run decisions")
        elif not self._confirm_lease(respect_release=False):
            raise LeaseLost("lease lost or database unavailable")
        async with self._lock:
            result = await asyncio.to_thread(self._tick_sync)
        await self.publish_live("decision")
        return result

    def _apply_slippage(self, side: str, price: float) -> float:
        """Backward-compatible slip-only helper (no latency/impact)."""
        q = quote_fill(side, price, slip_bps=self.params.slippage_bps, latency_ms=0)
        return q.fill_price

    def signal_flags(self) -> SignalFeatureFlags:
        return SignalFeatureFlags(
            confluence_enabled=bool(self.params.confluence_enabled),
            mtf_enabled=bool(self.params.mtf_enabled),
            kelly_enabled=bool(self.params.kelly_enabled),
            onchain_enabled=bool(self.params.onchain_enabled),
            super_opp_enabled=bool(self.params.super_opp_enabled),
            passive_wake_enabled=bool(self.params.passive_wake_enabled),
            post_trade_ai_enabled=bool(self.params.post_trade_ai_enabled),
            web_push_enabled=bool(self.params.web_push_enabled),
        )

    def _record_fidelity(self, q: Any, *, symbol: str, side: str) -> None:
        self.fidelity.record(
            FillSample(
                ts=getattr(q, "decision_ts", None) or datetime.now(timezone.utc),
                symbol=symbol,
                side=side,
                source=str(getattr(q, "source", "legacy_slip") or "legacy_slip"),
                slip_bps=float(getattr(q, "slip_bps", 0) or 0),
                impact_bps=float(getattr(q, "impact_bps", 0) or 0),
                filled_notional=float(getattr(q, "filled_notional", 0) or 0),
                unfilled_notional=float(getattr(q, "unfilled_notional", 0) or 0),
                rejected=bool(getattr(q, "rejected", False)),
                fee_bps=float(self.params.fee_bps),
            )
        )

    def fidelity_report(self) -> dict[str, Any]:
        return self.fidelity.report(
            twin_enabled=bool(getattr(self.params, "execution_twin_enabled", True)),
            fee_tier=str(getattr(self.params, "fee_tier", "us_intro")),
            taker_fee_bps=float(getattr(self.params, "taker_fee_bps", self.params.fee_bps)),
        )

    def _side_mark(self, symbol: str, side: str, fallback: float) -> float:
        """Buy against ask, sell against bid when twin quotes exist."""
        q = self.quotes.get(symbol) or {}
        if side == "buy":
            ask = float(q.get("ask") or 0)
            if ask > 0:
                return ask
        else:
            bid = float(q.get("bid") or 0)
            if bid > 0:
                return bid
        px = float(self.prices.get(symbol) or 0)
        return px if px > 0 else float(fallback)

    def _build_fill_quote(
        self,
        side: str,
        decision_price: float,
        *,
        notional: float,
        symbol: str | None = None,
    ) -> Any:
        twin = bool(getattr(self.params, "execution_twin_enabled", True))
        latency_ms = int(self.params.latency_ms)
        # Twin: always sleep then re-fetch L2 so fill is not the stale decision mid.
        if twin or (self.params.latency_wall_clock and latency_ms > 0):
            if latency_ms > 0:
                time.sleep(latency_ms / 1000.0)

        if twin and symbol:
            try:
                book = self.market.get_order_book(symbol, level=2)
            except Exception as exc:  # noqa: BLE001
                logger.warning("order book %s: %s — falling back to legacy slip", symbol, exc)
                book = None
            if book is not None:
                mid = float(book.get("mid") or decision_price or 0) or float(decision_price)
                fetched = book.get("fetched_at")
                age_ms = 0
                if isinstance(fetched, str):
                    try:
                        ft = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
                        age_ms = max(0, int((_utc_now_safe() - ft).total_seconds() * 1000))
                    except ValueError:
                        age_ms = 0
                q = walk_real_book(
                    side,
                    bids=book.get("bids") or [],
                    asks=book.get("asks") or [],
                    notional=float(notional),
                    decision_price=mid,
                    latency_ms=latency_ms,
                    min_fill_notional=float(
                        getattr(self.params, "twin_min_fill_notional", 15.0) or 15.0
                    ),
                    book_age_ms=age_ms,
                )
                self._record_fidelity(q, symbol=symbol, side=side)
                return q

        if bool(getattr(self.params, "order_book_depth_enabled", False)):
            q = walk_book_fill(
                side,
                decision_price,
                notional,
                latency_ms=latency_ms,
                depth_levels=int(getattr(self.params, "order_book_depth_levels", 5) or 5),
                base_slip_bps=float(self.params.slippage_bps),
            )
            if symbol:
                self._record_fidelity(q, symbol=symbol, side=side)
            return q

        impact = impact_bps_for_notional(
            notional,
            enabled=bool(self.params.order_impact_enabled),
            equity=max(self._equity(), 1.0),
            max_impact_bps=float(self.params.impact_bps_max),
        )
        q = quote_fill(
            side,
            decision_price,
            slip_bps=float(self.params.slippage_bps),
            latency_ms=latency_ms,
            impact_bps=impact,
            fill_mid=decision_price,
        )
        if symbol:
            self._record_fidelity(q, symbol=symbol, side=side)
        return q

    def _closed_sell_pnls(self) -> list[float]:
        return [
            float(t.realized_pnl)
            for t in self.wallet.trades
            if t.side == "sell" and getattr(t, "meta_complete", False)
        ]

    def _effective_risk_frac(self) -> float:
        """Risk per trade clamped to hard 2% — Kelly never raises the ceiling."""
        base = clamp_risk_per_trade_pct(float(self.params.risk_per_trade_pct))
        flags = self.signal_flags()
        if not flags.kelly_enabled:
            return base
        pnls = self._closed_sell_pnls()
        oos = evaluate_oos(pnls)
        kelly = evaluate_kelly(
            pnls,
            enabled=True,
            oos_activation_allowed=bool(oos.activation_allowed),
            allow_sizing=False,  # prod stays eval-only until sample+OOS explicitly unlocked
        )
        if kelly.active and kelly.suggested_size_mult:
            # Defensive path — currently unreachable while allow_sizing=False.
            scaled = base * float(kelly.suggested_size_mult)
            return clamp_risk_per_trade_pct(scaled)
        return base

    def _maybe_post_trade_review(self, trade: Trade) -> None:
        if trade.side != "sell":
            return
        flags = self.signal_flags()
        if not flags.post_trade_ai_enabled:
            return

        def _append(payload: dict[str, Any]) -> None:
            # Soft journal note — never mutates wallet / PnL.
            self.journal.add(
                symbol=trade.symbol,
                action="review",
                price=float(trade.price),
                qty=float(trade.qty),
                trade_id=trade.id,
                confidence=0.0,
                features={},
                top_features=[],
                explanation=str((payload.get("review") or {}).get("comment") or ""),
                note="post_trade_review",
                narrative=str((payload.get("review") or {}).get("comment") or ""),
            )

        run_post_trade_review(
            enabled=True,
            trade=trade.to_dict(),
            context={},
            append_journal=_append,
        )

    def _check_auto_kill(self, equity: float) -> None:
        if not self.params.kill_switch_enabled or self.killed:
            return
        self.peak_equity = max(self.peak_equity, equity)
        total_pnl_pct = ((equity - self.wallet.initial_balance) / self.wallet.initial_balance) * 100
        dd_pct = 0.0
        if self.peak_equity > 0:
            dd_pct = ((self.peak_equity - equity) / self.peak_equity) * 100
        if total_pnl_pct <= -self.params.max_daily_loss_pct:
            self.engage_kill_switch(f"max_loss_{total_pnl_pct:.2f}%")
        elif dd_pct >= self.params.max_drawdown_pct:
            self.engage_kill_switch(f"max_drawdown_{dd_pct:.2f}%")

    def _risk_score(self, equity: float) -> float:
        """0 = calm (blue), 1 = critical (red)."""
        pnl_pct = abs((equity - self.wallet.initial_balance) / self.wallet.initial_balance)
        dd = 0.0
        if self.peak_equity > 0:
            dd = (self.peak_equity - equity) / self.peak_equity
        raw = max(pnl_pct * 4 if equity < self.wallet.initial_balance else 0.0, dd * 3)
        if self.killed:
            raw = max(raw, 0.9)
        return float(min(1.0, raw))

    def watchlist(self) -> list[str]:
        symbols = [str(s).strip().upper() for s in self.params.symbols if str(s).strip()]
        deduped = list(dict.fromkeys(symbols))
        if deduped:
            return deduped
        return [self.params.symbol]

    def _next_symbol(self) -> str:
        book = self.watchlist()
        symbol = book[self._rr_index % len(book)]
        self._rr_index += 1
        self.active_symbol = symbol
        return symbol

    def _quote_symbols(self, focus: str) -> list[str]:
        book = self.watchlist()
        try:
            open_syms = list(self.wallet.positions.keys())
        except RuntimeError:
            open_syms = []
        if len(book) <= 4:
            raw = [*book, *open_syms, focus, self.params.symbol]
        else:
            raw = [focus, self.params.symbol, *open_syms]
        seen: list[str] = []
        for sym in raw:
            if sym and sym not in seen:
                seen.append(sym)
        return seen

    def _cache_ticker(self, symbol: str) -> float | None:
        try:
            ticker = self.market.get_ticker(symbol)
            price = float(ticker["price"])
            bid = float(ticker.get("bid") or price)
            ask = float(ticker.get("ask") or price)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ticker %s: %s", symbol, exc)
            return self.prices.get(symbol)
        self.prices[symbol] = price
        self.quotes[symbol] = {"bid": bid, "ask": ask, "last": price}
        return price

    def _equity(self) -> float:
        return float(self.wallet.equity(self.prices_map()))

    def _sell_qty(self, symbol: str, qty: float, price: float, note: str) -> Trade | None:
        self._require_fill_authority()
        pos = self.wallet.get_position(symbol)
        if pos is None or qty <= 0 or price <= 0:
            return None
        qty = min(qty, pos.qty)
        if pos.qty - qty < 1e-8:
            qty = pos.qty
        mark = self._side_mark(symbol, "sell", price)
        q = self._build_fill_quote("sell", mark, notional=qty * mark, symbol=symbol)
        if getattr(q, "rejected", False) or q.fill_price <= 0:
            logger.info("sell rejected %s: %s", symbol, getattr(q, "reject_reason", "empty"))
            return None
        if getattr(q, "filled_notional", 0) and q.fill_price > 0:
            filled_qty = float(q.filled_notional) / q.fill_price
            qty = min(qty, filled_qty)
        try:
            trade = self.wallet.sell(
                symbol,
                qty=qty,
                price=q.fill_price,
                fee_bps=self.params.fee_bps,
                note=note,
                decision_ts=q.decision_ts,
                decision_price=q.decision_price,
                latency_ms=q.latency_ms,
                slip_bps=q.slip_bps,
                impact_bps=q.impact_bps,
            )
        except ValueError:
            logger.warning("sell failed %s", symbol)
            return None
        if self.wallet.get_position(symbol) is None:
            self._exit_levels.pop(symbol, None)
        self._maybe_post_trade_review(trade)
        return trade

    def _levels_for(self, symbol: str) -> tuple[float, float]:
        stored = self._exit_levels.get(symbol)
        if stored:
            return float(stored["take_pct"]), float(stored["stop_pct"])
        return float(self.params.take_profit_pct), float(self.params.stop_loss_pct)

    def _decision_narrative(
        self,
        *,
        symbol: str,
        signal: SignalResult | None,
        plan: OrderPlan,
        order_placed: bool,
        price: float,
    ) -> str:
        detail = signal.detail if signal is not None else ""
        buy_th, sell_th = thresholds_from_detail(
            detail, self.params.buy_threshold, self.params.sell_threshold
        )
        features = signal.features if signal is not None else {}
        confidence = signal.confidence if signal is not None else 0.0
        signal_action = signal.action if signal is not None else plan.side
        return decision_narrative(
            symbol=symbol,
            signal_action=signal_action,
            confidence=confidence,
            features=features,
            plan_side=plan.side,
            plan_reason=plan.reason,
            order_placed=order_placed,
            buy_threshold=buy_th,
            sell_threshold=sell_th,
            base_buy_threshold=self.params.buy_threshold,
            base_sell_threshold=self.params.sell_threshold,
            rsi_oversold=self.params.rsi_oversold,
            rsi_overbought=self.params.rsi_overbought,
            auto_trade=self.params.auto_trade,
            killed=self.killed or plan.reason == "kill",
            price=price,
        )

    def _journal_fill(self, trade: Trade, signal: SignalResult | None, note: str) -> None:
        if signal is not None:
            explanation = self.strategy.explain(signal)
            features = signal.features
            top = self.strategy.top_features(features, n=5)
            confidence = signal.confidence
        else:
            features = {}
            top = []
            confidence = 1.0
            explanation = {
                "exit:tp": "Take-profit hit",
                "exit:sl": "Stop-loss hit",
                "exit:kill": "Kill switch flatten",
                "exit:manual": "Manual close",
            }.get(note, "Forced exit")
        if note and note not in explanation:
            explanation = f"{explanation} | {note}"
        narrative = self._decision_narrative(
            symbol=trade.symbol,
            signal=signal,
            plan=_plan_from_note(trade.side, note),
            order_placed=True,
            price=trade.price,
        )
        self.journal.add(
            symbol=trade.symbol,
            action=trade.side,
            price=trade.price,
            qty=trade.qty,
            trade_id=trade.id,
            confidence=confidence,
            features=features,
            top_features=top,
            explanation=explanation,
            simulated=True,
            source="live",
            note=note,
            narrative=narrative,
        )
        self.alerts.on_trade(trade.symbol, trade.side, trade.qty, trade.price)

    def _flatten_all(self) -> list[dict[str, Any]]:
        closed: list[dict[str, Any]] = []
        for sym in list(self.wallet.positions.keys()):
            pos = self.wallet.get_position(sym)
            if pos is None:
                continue
            price = self.prices.get(sym)
            if price is None:
                price = self._cache_ticker(sym)
            if price is None:
                price = pos.avg_entry
            trade = self._sell_qty(sym, pos.qty, price, "exit:kill")
            if trade is None:
                continue
            self._journal_fill(trade, None, "exit:kill")
            closed.append(
                {"symbol": sym, "trade_id": trade.id, "reason": "kill", "note": "exit:kill"}
            )
        return closed

    def _protect_positions(self) -> list[dict[str, Any]]:
        """Full exit on take-profit or stop-loss, even when the model says hold."""
        exits: list[dict[str, Any]] = []
        for sym in list(self.wallet.positions.keys()):
            pos = self.wallet.get_position(sym)
            if pos is None:
                continue
            price = self.prices.get(sym)
            if price is None:
                price = self._cache_ticker(sym)
            if price is None:
                continue
            take_pct, stop_pct = self._levels_for(sym)
            plan = plan_protective(price, pos.avg_entry, take_pct, stop_pct)
            if plan is None:
                continue
            trade = self._sell_qty(sym, pos.qty * plan.sell_fraction, price, plan.note)
            if trade is None:
                continue
            self._journal_fill(trade, None, plan.note)
            exits.append(
                {
                    "symbol": sym,
                    "trade_id": trade.id,
                    "reason": plan.reason,
                    "note": plan.note,
                    "price": trade.price,
                    "qty": trade.qty,
                }
            )
        return exits

    def _execute_signal(
        self,
        symbol: str,
        signal: SignalResult,
        price: float,
        candles: Any | None = None,
    ) -> tuple[Trade | None, OrderPlan]:
        now = datetime.now(timezone.utc)
        pos = self.wallet.get_position(symbol)
        equity = self._equity()
        pos_val = pos.market_value(price) if pos else 0.0
        plan = plan_signal(
            signal.action,
            signal.confidence,
            has_position=pos is not None,
            position_value=pos_val,
            equity=equity,
            max_position_pct=self.params.max_position_pct,
            position_size_pct=self.params.position_size_pct,
            open_symbols=len(self.wallet.positions),
            max_open_symbols=self._effective_max_open(equity),
            cooldown_active=(
                cooldown_active(self._last_buy_at.get(symbol), now, self.params.add_cooldown_min)
                if pos is not None
                else False
            ),
            aggressive_sell_confidence=self.params.aggressive_sell_confidence,
            sell_threshold=self.params.sell_threshold,
        )
        if plan.side == "hold":
            return None, plan

        if plan.side == "buy":
            if not self._symbol_tradeable(symbol, price):
                return None, OrderPlan("hold", 0.0, "budget", "hold:illiquid")
            gate = evaluate_entry_gates(
                confluence_enabled=bool(self.params.confluence_enabled),
                mtf_enabled=bool(self.params.mtf_enabled),
                features=signal.features,
                intended_side="buy",
                candles=candles,
                trade_pnls=self._closed_sell_pnls(),
            )
            if gate.shadow:
                logger.info("entry_gate shadow %s: %s", symbol, gate.reason)
            if gate.blocked:
                logger.info("entry_gate block %s: %s", symbol, gate.reason)
                return None, OrderPlan("hold", 0.0, "signal_gate", f"hold:gate:{gate.reason[:48]}")
            open_r = portfolio_open_risk_r(
                self.wallet.positions,
                self.prices_map(),
                self._exit_levels,
                equity,
                float(self.params.stop_loss_pct),
            )
            if open_r >= float(self.params.max_portfolio_risk_r):
                return None, OrderPlan("hold", 0.0, "portfolio_risk", "hold:portfolio_risk")
            levels = compute_exit_levels(
                price,
                candles,
                fee_bps=self.params.fee_bps,
                slippage_bps=self.params.slippage_bps,
                stop_atr_mult=float(self.params.atr_stop_mult),
                reward_risk=float(self.params.reward_risk),
                floor_stop_pct=max(0.008, float(self.params.stop_loss_pct) * 0.5),
                cap_stop_pct=0.04,
            )
            # Provisional ask mark for sizing (no twin sleep yet).
            ask_mark = self._side_mark(symbol, "buy", price)
            fill = self._apply_slippage("buy", ask_mark)
            risk_frac = self._effective_risk_frac()
            assert risk_frac <= HARD_RISK_PER_TRADE_PCT + 1e-12
            risk_spend = risk_position_spend(
                equity,
                self.wallet.cash,
                fill,
                levels.stop_pct,
                risk_frac=risk_frac,
                max_position_pct=float(self.params.max_position_pct),
            )
            soft_cap = buy_spend(
                self.wallet.cash,
                equity,
                pos_val,
                max(self.params.position_size_pct, 0.15),
                self.params.max_position_pct,
            )
            spend = min(risk_spend, soft_cap) if soft_cap > 0 else risk_spend
            # Room left under the per-symbol equity cap.
            room = max(0.0, self.params.max_position_pct * equity - pos_val)
            spend = min(spend, room, self.wallet.cash)
            fee_mult = 1.0 + self.params.fee_bps / 10_000.0
            if spend < 15 or fill <= 0:
                return None, OrderPlan("hold", 0.0, "budget", "hold:budget")
            q = self._build_fill_quote("buy", ask_mark, notional=spend, symbol=symbol)
            if getattr(q, "rejected", False) or q.fill_price <= 0:
                return None, OrderPlan(
                    "hold", 0.0, "budget", "hold:insufficient_depth"
                )
            fill = q.fill_price
            filled_spend = float(getattr(q, "filled_notional", 0) or spend)
            filled_spend = min(filled_spend, spend)
            qty = filled_spend / (fill * fee_mult)
            self._require_fill_authority()
            try:
                trade = self.wallet.buy(
                    symbol,
                    qty=qty,
                    price=fill,
                    fee_bps=self.params.fee_bps,
                    note=plan.note,
                    decision_ts=q.decision_ts,
                    decision_price=q.decision_price,
                    latency_ms=q.latency_ms,
                    slip_bps=q.slip_bps,
                    impact_bps=q.impact_bps,
                )
            except ValueError:
                return None, OrderPlan("hold", 0.0, "budget", "hold:budget")
            self._last_buy_at[symbol] = now
            self._exit_levels[symbol] = annotate_levels(levels)
            logger.info(
                "entry %s stop=%.2f%% take=%.2f%% atr_pct=%s spend=%.2f",
                symbol,
                levels.stop_pct * 100,
                levels.take_pct * 100,
                f"{levels.atr_pct * 100:.2f}%" if levels.atr_pct else "n/a",
                spend,
            )
            return trade, plan

        if pos is None:
            return None, OrderPlan("hold", 0.0, "flat", "hold:flat")

        # Discretionary signal exits: never dump at a net loss — SL/TP own that.
        last_buy = self._last_buy_at.get(symbol)
        min_hold = int(self.params.min_hold_min)
        if min_hold > 0 and last_buy is not None:
            if (now - last_buy).total_seconds() < min_hold * 60:
                return None, OrderPlan("hold", 0.0, "min_hold", "hold:min_hold")
        if not sell_is_net_positive(
            pos.avg_entry,
            price,
            fee_bps=self.params.fee_bps,
            slippage_bps=self.params.slippage_bps,
        ):
            return None, OrderPlan("hold", 0.0, "fee_guard", "hold:fee_guard")
        plan = OrderPlan("sell", 1.0, "signal", "exit:signal")
        trade = self._sell_qty(symbol, pos.qty, price, plan.note)
        if trade is None:
            return None, OrderPlan("hold", 0.0, "flat", "hold:flat")
        return trade, plan

    def _tick_sync(self) -> dict[str, Any]:
        self._ensure_universe(force=False)
        symbol = self._next_symbol()
        for sym in self._quote_symbols(symbol):
            self._cache_ticker(sym)
        primary = self.prices.get(self.params.symbol)
        if primary is not None:
            self.last_price = primary

        if self.killed and self.wallet.positions:
            self._flatten_all()
        else:
            self._check_auto_kill(self._equity())

        exits: list[dict[str, Any]] = []
        if self.params.auto_trade and not self.killed:
            exits = self._protect_positions()

        price = self.prices.get(symbol)
        if price is None:
            raise RuntimeError(f"no price for {symbol}")

        candles = self.market.get_candles(
            symbol,
            granularity=self.params.candle_granularity_sec,
            limit=self.params.candle_lookback,
        )
        try:
            train_info = self.strategy.train(candles, self.params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("train failed (%s); falling back to heuristic", exc)
            train_info = {"ok": False, "reason": f"train_exception:{exc}"}
            self.strategy.model = None
        try:
            self.news.refresh_if_stale()
        except Exception as exc:  # noqa: BLE001
            logger.warning("news refresh: %s", exc)
        signal = self.strategy.predict(candles, self.params, news=self.news.cached_brief())
        # Universe scan entries: open small risk-sized buys when flat so the book
        # is not stuck on sell-only signals with nothing to sell.
        signal = self._scan_entry_bias(signal, symbol)
        self.last_signal = signal
        self.last_signal_symbol = symbol
        self.last_tick_at = datetime.now(timezone.utc)
        self.last_error = None
        self._universe_signals[symbol] = {
            "action": signal.action,
            "confidence": float(signal.confidence),
            "ts": self.last_tick_at.isoformat(),
            "from_news": any(h.get("symbol") == symbol for h in self.universe.news_hits),
        }

        # Passiveive wake (lot 4): deterministic threshold; journal when AI should wake.
        if self.params.passive_wake_enabled:
            move = None
            try:
                if candles is not None and len(candles) >= 2 and "close" in candles.columns:
                    c0 = float(candles["close"].iloc[-2])
                    c1 = float(candles["close"].iloc[-1])
                    if c0 > 0:
                        move = (c1 - c0) / c0
            except Exception:  # noqa: BLE001
                move = None
            wake = evaluate_passive_wake(enabled=True, move_pct=move)
            if wake.should_wake_ai:
                logger.info("passive_wake %s value=%s", symbol, wake.value)
                self.journal.add(
                    symbol=symbol,
                    action="wake",
                    price=float(price),
                    qty=0.0,
                    trade_id=None,
                    confidence=0.0,
                    features={},
                    top_features=[],
                    explanation=wake.reason,
                    note="passive_wake",
                    narrative=wake.reason,
                )

        if not self.killed:
            self.alerts.on_strong_signal(symbol, signal.action, signal.confidence)

        protected = next((item for item in exits if item["symbol"] == symbol), None)
        trade: Trade | None = None
        if protected is not None:
            plan = OrderPlan("sell", 1.0, str(protected["reason"]), str(protected["note"]))
        elif self.killed or not self.params.auto_trade:
            plan = OrderPlan("hold", 0.0, "kill" if self.killed else "hold", "")
        else:
            trade, plan = self._execute_signal(symbol, signal, price, candles=candles)
            if trade is not None:
                self._journal_fill(trade, signal, trade.note or plan.note)

        self._check_auto_kill(self._equity())
        state = self.account_state()
        self.alerts.on_pnl(float(state["total_pnl"]))

        explanation = self.strategy.explain(signal)
        if plan.note and plan.note not in explanation:
            explanation = f"{explanation} | {plan.note}"
        trade_id = trade.id if trade else (protected["trade_id"] if protected else None)
        order_placed = trade is not None or protected is not None
        buy_th, sell_th = thresholds_from_detail(
            signal.detail, self.params.buy_threshold, self.params.sell_threshold
        )
        narrative = self._decision_narrative(
            symbol=symbol,
            signal=signal,
            plan=plan,
            order_placed=order_placed,
            price=price,
        )
        extra_exits = format_other_exits(exits, symbol)
        if extra_exits:
            narrative = f"{narrative} {extra_exits}"
        entry = {
            "ts": self.last_tick_at.isoformat(),
            "symbol": symbol,
            "price": price,
            "signal": signal.to_dict(),
            "decision": {
                "action": plan.side,
                "reason": plan.reason,
                "note": plan.note,
                "sell_fraction": plan.sell_fraction,
                "order_placed": order_placed,
                "buy_threshold": buy_th,
                "sell_threshold": sell_th,
            },
            "exits": exits,
            "train": train_info,
            "trade_id": trade_id,
            "explanation": explanation,
            "narrative": narrative,
            "killed": self.killed,
        }
        self.signal_history.append(entry)
        self.signal_history = self.signal_history[-100:]
        self._persist_state()
        return entry

    def _symbol_tradeable(self, symbol: str, price: float) -> bool:
        """Prefer liquid majors / top volume; reject broken or dust prices."""
        if price <= 0 or price < 1e-8:
            return False
        core = {"BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "LINK-USD", "AVAX-USD", "DOGE-USD", "ADA-USD"}
        if symbol in core:
            return True
        if any(h.get("symbol") == symbol for h in self.universe.news_hits):
            return True
        row = next((r for r in self.universe.rows if r.symbol == symbol), None)
        if row is None:
            return False
        # Top half of the volume book only — skip illiquid meme tails.
        return int(row.rank) <= max(15, int(self.params.universe_size) // 3) and float(
            row.volume_24h or 0
        ) >= float(self.params.universe_min_volume_usd)

    def _scan_entry_bias(self, signal: SignalResult, symbol: str) -> SignalResult:
        """Style-aware scan entries (prudent / intermediaire / agressif)."""
        if self.wallet.get_position(symbol) is not None:
            return signal
        if signal.action == "buy":
            return signal
        style = normalize_style(self.params.trading_style)
        g = scan_gates(style)
        buy_p = float(signal.probs.get("buy", 0.0) or 0.0)
        sell_p = float(signal.probs.get("sell", 0.0) or 0.0)
        rsi = float(signal.features.get("rsi", 50.0) or 50.0)
        macd_hist = float(signal.features.get("macd_hist", 0.0) or 0.0)
        news_hit = next(
            (h for h in self.universe.news_hits if h.get("symbol") == symbol),
            None,
        )
        if rsi >= float(self.params.rsi_overbought) and sell_p >= 0.55:
            return signal

        reason = ""
        conf = float(signal.confidence)
        if (
            rsi <= float(self.params.rsi_oversold) + g["rsi_extra"]
            and sell_p < g["sell_cap"]
            and buy_p >= g["min_buy_p_rsi"]
        ):
            reason = "entry:rsi_scan"
            conf = max(buy_p, float(self.params.buy_threshold))
        elif buy_p >= g["min_buy_p_edge"] and buy_p >= sell_p + g["edge_gap"]:
            reason = "entry:prob_scan"
            conf = buy_p
        elif (
            g["allow_weak_prob"] >= 1.0
            and buy_p >= 0.22
            and buy_p + 0.02 >= sell_p * 0.4
            and sell_p < 0.8
        ):
            reason = "entry:prob_scan"
            conf = max(buy_p, 0.4)
        elif (
            g["allow_macd"] >= 1.0
            and macd_hist > 0
            and g["macd_rsi_lo"] <= rsi <= g["macd_rsi_hi"]
            and buy_p >= sell_p
            and buy_p >= g["min_buy_p_macd"]
        ):
            reason = "entry:macd_scan"
            conf = max(buy_p, 0.48)
        elif (
            news_hit is not None
            and float(news_hit.get("score") or 0) >= g["news_score"]
            and rsi < g["news_rsi_cap"]
            and sell_p < g["sell_cap"]
        ):
            reason = "entry:news_scan"
            conf = max(buy_p, float(self.params.buy_threshold) * 0.95)
        if not reason:
            return signal
        return SignalResult(
            action="buy",
            confidence=float(conf),
            probs=dict(signal.probs),
            features=dict(signal.features),
            model_ready=signal.model_ready,
            detail=f"{signal.detail} | {reason}|style={style}",
        )

    def update_params(self, patch: dict[str, Any]) -> BotParams:
        if self.lease is None:
            self._reload_params_only()
        data = self.params.model_dump()
        clean = {k: v for k, v in patch.items() if v is not None}
        explicit_book = "symbols" in clean or "watchlist" in clean
        if "symbols" not in clean and "watchlist" in clean:
            clean["symbols"] = clean["watchlist"]
        clean.pop("watchlist", None)
        if "symbol" in clean and not explicit_book:
            sym = str(clean["symbol"]).strip().upper()
            current = [str(s).strip().upper() for s in data.get("symbols") or []]
            if sym and sym not in current:
                clean["symbols"] = [sym, *current]
        style_requested = "trading_style" in clean
        if style_requested:
            style = normalize_style(clean.get("trading_style"))
            data = apply_style_preset(data, style)
            clean.pop("trading_style", None)
        allowed = set(BotParams.model_fields)
        data.update({k: v for k, v in clean.items() if k in allowed})
        if "risk_per_trade_pct" in data:
            data["risk_per_trade_pct"] = clamp_risk_per_trade_pct(
                float(data["risk_per_trade_pct"])
            )
        if style_requested:
            data["trading_style"] = normalize_style(data.get("trading_style"))
        new_params = BotParams(**data)
        balance_changed = new_params.initial_balance != self.params.initial_balance
        self.params = new_params
        if self.params.universe_enabled:
            self.universe.size = int(self.params.universe_size)
            self.universe.min_volume_usd = float(self.params.universe_min_volume_usd)
        if balance_changed and not self.wallet.trades and not self.wallet.positions:
            self.wallet.reset(new_params.initial_balance)
            self.peak_equity = new_params.initial_balance
            self._last_buy_at.clear()
        self._save_params()
        return self.params

    def update_alerts(self, patch: dict[str, Any]) -> AlertParams:
        if self.lease is None:
            self._reload_params_only()
        updated = self.alerts.update_params(patch)
        self._save_params()
        return updated

    def _apply_pending_reset(self) -> None:
        """Apply an API reset flag. Only the live lease owner writes the book."""
        if self.store is None or self.lease is None:
            return
        requested, balance = self.store.peek_reset_request()
        if not requested:
            return
        if not self._confirm_lease(respect_release=False):
            raise LeaseLost("reset refused: lease not held")
        self._reset_owned(balance)

    def _apply_pending_closes(self) -> list[dict[str, Any]]:
        """Apply queued manual closes. Only the live lease owner may fill."""
        if self.store is None or self.lease is None:
            return []
        symbols = self.store.peek_close_requests()
        if not symbols:
            return []
        if not self._confirm_lease(respect_release=False):
            raise LeaseLost("close refused: lease not held")
        closed: list[dict[str, Any]] = []
        done: list[str] = []
        for sym in symbols:
            result = self._close_position_owned(sym)
            done.append(sym)
            closed.append(result)
        self.store.clear_close_requests(done)
        if closed:
            self._persist_state()
        return closed

    def _close_position_owned(self, symbol: str) -> dict[str, Any]:
        sym = symbol.strip().upper()
        pos = self.wallet.get_position(sym)
        if pos is None:
            return {
                "ok": True,
                "symbol": sym,
                "closed": False,
                "reason": "flat",
                "trade": None,
            }
        price = self.prices.get(sym)
        if price is None:
            price = self._cache_ticker(sym)
        if price is None:
            price = float(pos.avg_entry)
        trade = self._sell_qty(sym, pos.qty, float(price), "exit:manual")
        if trade is None:
            return {
                "ok": False,
                "symbol": sym,
                "closed": False,
                "reason": "sell_failed",
                "trade": None,
            }
        self._journal_fill(trade, None, "exit:manual")
        return {
            "ok": True,
            "symbol": sym,
            "closed": True,
            "reason": "manual",
            "trade": trade.to_dict(),
        }

    def close_position(self, symbol: str) -> dict[str, Any]:
        """Fully close one paper position.

        API process (store, no lease) enqueues the symbol; the trader fills it.
        Legacy JSON mode (no store) closes immediately.
        """
        sym = str(symbol or "").strip().upper()
        if not sym:
            return {"ok": False, "symbol": "", "closed": False, "reason": "invalid_symbol"}
        leased = self.store is not None and self.lease is not None and self._confirm_lease(
            respect_release=False
        )
        if self.store is not None and not leased:
            pending = self.store.enqueue_close(sym)
            state = self.account_state()
            return {
                "ok": True,
                "symbol": sym,
                "closed": False,
                "queued": True,
                "pending": pending,
                "reason": "queued",
                "account": state,
            }
        result = self._close_position_owned(sym)
        self._persist_state()
        result["queued"] = False
        result["account"] = self.account_state()
        return result

    def _reset_owned(self, initial_balance: float | None) -> dict[str, Any]:
        bal = initial_balance if initial_balance is not None else self.params.initial_balance
        self.params.initial_balance = bal
        self.wallet.reset(bal)
        self.peak_equity = bal
        self.signal_history.clear()
        self.last_signal = None
        self.last_signal_symbol = None
        self.active_symbol = self.params.symbol
        self._rr_index = 0
        self._last_buy_at.clear()
        self._exit_levels.clear()
        self.journal.clear()
        self.alerts.reset_pnl_tracker()
        self.killed = False
        self.kill_reason = None
        self.equity_history.reset_session()
        self.equity_history.maybe_record(
            equity=bal,
            cash=bal,
            positions=0,
            pnl=0.0,
            peak_equity=bal,
            force=True,
        )
        clear_state()
        self._save_params()
        self._persist_state()
        if self.store is not None:
            self.store.clear_reset_request()
        return self.account_state()

    def reset_account(self, initial_balance: float | None = None) -> dict[str, Any]:
        """Reset the paper book only when this process holds the lease.

        The API process has a store and no lease. It records ``reset_requested``
        and leaves positions and trades unchanged. The trader applies the flag
        between ticks, then ``save_runtime``. A process with no store (legacy
        JSON) still resets locally.
        """
        leased = self.store is not None and self.lease is not None and self._confirm_lease(
            respect_release=False
        )
        if self.store is not None and not leased:
            self.store.request_reset(initial_balance)
            state = self.account_state()
            state["reset_accepted"] = True
            state["reset_requested"] = True
            return state
        return self._reset_owned(initial_balance)

    def prices_map(self, *, refresh_open: bool = False) -> dict[str, float]:
        """Mark prices for the book.

        ``refresh_open=True`` pulls a fresh ticker for every open symbol so the
        UI unrealized PnL is not stuck at entry (common on the API process after
        hydrate, when ``self.prices`` is empty).
        """
        if refresh_open:
            for sym in list(self.wallet.positions.keys()):
                self._cache_ticker(sym)
        prices = dict(self.prices)
        if self.params.symbol not in prices:
            if self.last_price is not None:
                prices[self.params.symbol] = self.last_price
            else:
                px = self._cache_ticker(self.params.symbol)
                if px is not None:
                    prices[self.params.symbol] = px
        for sym, pos in self.wallet.positions.items():
            # Prefer a live mark; only fall back to entry when the ticker failed.
            if sym not in prices or prices[sym] <= 0:
                px = self._cache_ticker(sym)
                if px is not None and px > 0:
                    prices[sym] = px
                else:
                    prices.setdefault(sym, pos.avg_entry)
        return prices

    def _bot_running_for_ui(self) -> bool:
        """API process has no local loops; UI must follow trader lease when Store is wired."""
        if self.running:
            return True
        if self.store is None:
            return False
        try:
            held = bool(read_lease().get("held"))
            self._db_ok = True
            return held
        except Exception:  # noqa: BLE001
            self._db_ok = False
            return False

    def status(self) -> dict[str, Any]:
        bot_running = self._bot_running_for_ui()
        return {
            "bot_running": bot_running,
            "executor": (
                "trader" if self.lease is not None else ("api" if self.store is not None else "legacy")
            ),
            "killed": self.killed,
            "kill_reason": self.kill_reason,
            "auto_trade": self.params.auto_trade,
            "symbol": self.params.symbol,
            "active_symbol": self.active_symbol,
            "symbols": self.watchlist(),
        }

    def system_status(self) -> dict[str, Any]:
        """Human-facing feature board for the dashboard."""
        now = datetime.now(timezone.utc)
        features: list[dict[str, Any]] = []

        def add(fid: str, label: str, state: str, detail: str) -> None:
            features.append({"id": fid, "label": label, "state": state, "detail": detail})

        add("engine", "Moteur", "ok", "API paper en ligne (mode simulation)")

        if self.killed:
            add(
                "kill",
                "Arrêt d’urgence",
                "error",
                f"Actif — {self.kill_reason or 'raison inconnue'}. Aucun nouvel ordre.",
            )
        else:
            add("kill", "Arrêt d’urgence", "ok", "Inactif — trading autorisé côté sécurité")

        lease_held = self.running
        lease_note = "processus local"
        if self.store is not None:
            try:
                snap = read_lease()
                lease_held = bool(snap.get("held"))
                owner = snap.get("owner_id") or "aucun"
                lease_note = f"bail {owner}" + ("" if lease_held else " (expiré ou libre)")
                self._db_ok = True
            except Exception as exc:  # noqa: BLE001
                self._db_ok = False
                lease_held = False
                lease_note = f"bail illisible ({exc})"

        if self.running or lease_held:
            add(
                "bot_loop",
                "Boucle bot",
                "ok",
                f"Active · {lease_note} · toutes les {self.params.decision_interval_sec}s",
            )
        else:
            add("bot_loop", "Boucle bot", "off", f"Arrêtée · {lease_note}")

        if self.params.auto_trade and not self.killed:
            add("auto_trade", "Trading automatique", "ok", "Les ordres paper peuvent être placés")
        elif self.killed:
            add("auto_trade", "Trading automatique", "error", "Bloqué par l’arrêt d’urgence")
        else:
            add(
                "auto_trade",
                "Trading automatique",
                "warn",
                "OFF — analyse possible, mais aucun ordre",
            )

        tp = float(self.params.take_profit_pct) * 100
        sl = float(self.params.stop_loss_pct) * 100
        add(
            "tp_sl",
            "TP / SL micro",
            "ok",
            f"Prise de gain {tp:.2f}% · stop-loss {sl:.2f}% · cooldown {self.params.add_cooldown_min} min",
        )

        book = self.watchlist()
        add(
            "watchlist",
            "Multi-paires",
            "ok" if len(book) > 1 else "warn",
            f"{', '.join(book)} · actif {self.active_symbol}",
        )

        try:
            brief = self.news.cached_brief()
            headlines = getattr(brief, "headlines", ()) or ()
            n = len(headlines)
            bias = float(getattr(brief, "overall_bias", getattr(brief, "overall_score", 0.0)) or 0.0)
            add(
                "news",
                "Actualités",
                "ok" if n > 0 else "warn",
                f"{n} titres en cache · biais {bias:+.2f}",
            )
        except Exception as exc:  # noqa: BLE001
            add("news", "Actualités", "warn", f"Lecture news: {exc}")

        if self.store is not None:
            if self._db_ok:
                add(
                    "persist",
                    "Sauvegarde paper",
                    "ok",
                    "Postgres est la source de vérité (niveaux de sortie inclus). JSON du volume = copie de secours.",
                )
            else:
                add(
                    "persist",
                    "Sauvegarde paper",
                    "error",
                    "Postgres injoignable — le trader ne place pas d'ordres",
                )
        elif STATE_PATH.exists():
            add(
                "persist",
                "Sauvegarde paper",
                "ok",
                f"État disque présent ({STATE_PATH.name}) — survit à un redémarrage",
            )
        else:
            add(
                "persist",
                "Sauvegarde paper",
                "warn",
                "Pas encore de fichier d’état — sera créé au prochain tick",
            )

        twin_on = bool(getattr(self.params, "execution_twin_enabled", True))
        fid = self.fidelity_report()
        if twin_on:
            gate = "ok" if fid.get("go_live_ready") else "warn"
            add(
                "execution_twin",
                "Jumeau L2",
                gate,
                f"Twin Coinbase L2 · tier {fid.get('fee_tier')} · taker {fid.get('taker_fee_bps')} bps · "
                f"fills {fid.get('twin_fill_count')} · {fid.get('message_fr')}",
            )
        else:
            add(
                "execution_twin",
                "Jumeau L2",
                "warn",
                "OFF — fills legacy slip (paper trop rose vs live)",
            )

        if self.last_tick_at is None:
            add("last_tick", "Dernière décision", "warn", "Aucun tick encore")
        else:
            age = max(0.0, (now - self.last_tick_at).total_seconds())
            limit = max(90.0, float(self.params.decision_interval_sec) * 3)
            if self.last_error:
                add(
                    "last_tick",
                    "Dernière décision",
                    "error",
                    f"Il y a {int(age)}s — erreur: {self.last_error}",
                )
            elif age > limit and self.running:
                add(
                    "last_tick",
                    "Dernière décision",
                    "warn",
                    f"Il y a {int(age)}s — boucle peut-être lente",
                )
            else:
                add(
                    "last_tick",
                    "Dernière décision",
                    "ok",
                    f"Il y a {int(age)}s · {self.last_signal_symbol or self.params.symbol}",
                )

        open_n = len(self.wallet.positions)
        add(
            "positions",
            "Positions ouvertes",
            "ok" if open_n > 0 else "off",
            f"{open_n} position(s) · cash {self.wallet.cash:,.2f} $",
        )

        ranks = {"ok": 0, "off": 1, "warn": 2, "error": 3}
        overall = "ok"
        for f in features:
            if ranks.get(str(f["state"]), 0) > ranks[overall]:
                overall = str(f["state"])

        return {
            "overall": overall,
            "checked_at": now.isoformat(),
            "features": features,
            **self.status(),
        }

    def account_state(self) -> dict[str, Any]:
        if self.lease is None and self.store is not None:
            try:
                self.hydrate()
            except Exception as exc:  # noqa: BLE001
                self._db_ok = False
                self.last_error = f"store read failed: {exc}"
        # API process has no price loop — always refresh open marks for truthful PnL.
        refresh_open = self.lease is None
        prices = self.prices_map(refresh_open=refresh_open)
        snap = self.wallet.snapshot(prices)
        # Net PnL if closed now (entry fee + exit fee + sell slippage).
        enriched: list[dict[str, Any]] = []
        for row in snap.get("positions") or []:
            if not isinstance(row, dict):
                continue
            cost_basis = row.get("cost_basis")
            net = net_pnl_if_closed(
                float(row.get("avg_entry") or 0),
                float(row.get("qty") or 0),
                float(row.get("mark_price") or 0),
                fee_bps=float(self.params.fee_bps),
                slippage_bps=float(self.params.slippage_bps),
                cost_basis=float(cost_basis) if cost_basis is not None else None,
            )
            enriched.append(
                {
                    **row,
                    "gross_pnl": net["gross_pnl"],
                    "net_pnl": net["net_pnl"],
                    "net_pnl_pct": net["net_pnl_pct"],
                    "fees_total": net["fees_total"],
                    "would_be_green": net["would_be_green"],
                    "mark_ts": row.get("mark_ts"),
                }
            )
        snap["positions"] = enriched
        equity = float(snap["equity"])
        self.peak_equity = max(self.peak_equity, equity)
        dd = 0.0
        if self.peak_equity > 0:
            dd = ((self.peak_equity - equity) / self.peak_equity) * 100
        return {
            **snap,
            "fee_bps": self.params.fee_bps,
            "slippage_bps": self.params.slippage_bps,
            "latency_ms": self.params.latency_ms,
            "order_impact_enabled": self.params.order_impact_enabled,
            "marks_as_of": datetime.now(timezone.utc).isoformat(),
            "signal_flags": self.signal_flags().as_dict(),
            "symbol": self.params.symbol,
            "symbols": self.watchlist(),
            "active_symbol": self.active_symbol,
            "prices": prices,
            "last_price": self.last_price,
            "bot_running": self._bot_running_for_ui(),
            "auto_trade": self.params.auto_trade,
            "killed": self.killed,
            "kill_reason": self.kill_reason,
            "peak_equity": self.peak_equity,
            "drawdown_pct": dd,
            "risk_score": self._risk_score(equity),
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "last_error": self.last_error,
            "max_open_symbols": self._effective_max_open(equity),
            "open_count": len(self.wallet.positions),
            "universe_size": len(self.watchlist()),
        }
