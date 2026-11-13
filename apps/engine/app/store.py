"""Postgres persistence for paper trading runtime state."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from psycopg import errors as pg_errors
from psycopg.types.json import Json

from app.config import AlertParams, BotParams, LlmSettings
from app.db import get_conn
from app.journal import DecisionJournal
from app.persist import load_state, parse_last_buys
from app.runtime import TradingRuntime
from app.wallet import PaperWallet, Position, Trade

logger = logging.getLogger(__name__)

_SINGLETON_ID = 1
# Serialize writers that touch account / equity / book (API kill + trader persist).
_PAPER_BOOK_LOCK_KEY = 87231401
_DEADLOCK_RETRIES = 5
_DEADLOCK_SLEEP_SEC = 0.05

T = TypeVar("T")


def _is_deadlock(exc: BaseException) -> bool:
    if isinstance(exc, pg_errors.DeadlockDetected):
        return True
    msg = str(exc).lower()
    return "deadlock detected" in msg


def _with_deadlock_retry(label: str, fn: Callable[[], T]) -> T:
    last: BaseException | None = None
    for attempt in range(1, _DEADLOCK_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if not _is_deadlock(exc) or attempt >= _DEADLOCK_RETRIES:
                raise
            last = exc
            logger.warning(
                "%s deadlock (attempt %s/%s); retrying",
                label,
                attempt,
                _DEADLOCK_RETRIES,
            )
            time.sleep(_DEADLOCK_SLEEP_SEC * attempt)
    assert last is not None
    raise last


def _lock_paper_book(cur) -> None:
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (_PAPER_BOOK_LOCK_KEY,))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(raw: str | None) -> datetime:
    if not raw:
        return _utc_now()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return _utc_now()


def _account_exists(cur) -> bool:
    cur.execute("SELECT 1 FROM account WHERE id = %s", (_SINGLETON_ID,))
    return cur.fetchone() is not None


def _exit_meta_from_levels(levels: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in levels.items() if k not in ("stop_pct", "take_pct")}


class Store:
    """Load/save paper runtime, params, and control audit rows in Postgres."""

    def maybe_migrate_json(self) -> bool:
        """One-shot import from paper_state.json when account row is missing."""
        data = load_state()
        if not data:
            return False
        return self.import_paper_state(data)

    def import_paper_state(self, data: dict[str, Any]) -> bool:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if _account_exists(cur):
                    return False
                self._apply_paper_json(cur, data, mark_migrated=True)
            conn.commit()
        logger.info("imported paper_state.json into Postgres")
        return True

    def load_runtime(self, runtime: TradingRuntime) -> bool:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT mode, cash, initial_balance, realized_pnl, peak_equity,
                           killed, kill_reason, session_started_at
                    FROM account WHERE id = %s
                    """,
                    (_SINGLETON_ID,),
                )
                acct = cur.fetchone()
                if acct is None:
                    return False

                (
                    mode,
                    cash,
                    initial_balance,
                    realized_pnl,
                    peak_equity,
                    killed,
                    kill_reason,
                    session_started_at,
                ) = acct
                if str(mode) != "paper":
                    raise RuntimeError(f"unsupported account mode: {mode}")

                runtime.wallet = PaperWallet(
                    initial_balance=float(initial_balance),
                    cash=float(cash),
                    realized_pnl=float(realized_pnl),
                )
                runtime.peak_equity = float(peak_equity)
                runtime.killed = bool(killed)
                runtime.kill_reason = str(kill_reason) if kill_reason else None

                cur.execute(
                    """
                    SELECT symbol, qty, avg_entry, stop_pct, take_pct, exit_meta, cost_basis
                    FROM positions ORDER BY symbol
                    """
                )
                runtime.wallet.positions.clear()
                runtime._exit_levels.clear()
                for symbol, qty, avg_entry, stop_pct, take_pct, exit_meta, cost_basis in cur.fetchall():
                    sym = str(symbol)
                    avg = float(avg_entry)
                    qty_f = float(qty)
                    basis = float(cost_basis) if cost_basis is not None else avg * qty_f
                    runtime.wallet.positions[sym] = Position(
                        symbol=sym,
                        qty=qty_f,
                        avg_entry=avg,
                        cost_basis=basis,
                    )
                    levels: dict[str, Any] = {}
                    if stop_pct is not None:
                        levels["stop_pct"] = float(stop_pct)
                    if take_pct is not None:
                        levels["take_pct"] = float(take_pct)
                    if isinstance(exit_meta, dict):
                        for key, val in exit_meta.items():
                            if key not in levels:
                                levels[key] = val
                    if levels:
                        runtime._exit_levels[sym] = levels

                cur.execute(
                    """
                    SELECT id, symbol, side, qty, price, fee, ts, realized_pnl, note,
                           decision_ts, decision_price, latency_ms, slip_bps, impact_bps,
                           invested_quote, avg_entry_ref, cost_basis_closed, reference_ts,
                           meta_complete
                    FROM trades ORDER BY ts
                    """
                )
                runtime.wallet.trades.clear()
                for row in cur.fetchall():
                    (
                        tid,
                        symbol,
                        side,
                        qty,
                        price,
                        fee,
                        ts,
                        realized_pnl,
                        note,
                        decision_ts,
                        decision_price,
                        latency_ms,
                        slip_bps,
                        impact_bps,
                        invested_quote,
                        avg_entry_ref,
                        cost_basis_closed,
                        reference_ts,
                        meta_complete,
                    ) = row
                    runtime.wallet.trades.append(
                        Trade.from_dict(
                            {
                                "id": str(tid),
                                "symbol": str(symbol),
                                "side": str(side),
                                "qty": float(qty),
                                "price": float(price),
                                "fee": float(fee),
                                "ts": ts if isinstance(ts, datetime) else ts,
                                "realized_pnl": float(realized_pnl),
                                "note": str(note or ""),
                                "decision_ts": decision_ts,
                                "decision_price": decision_price,
                                "latency_ms": latency_ms,
                                "slip_bps": slip_bps,
                                "impact_bps": impact_bps,
                                "invested_quote": invested_quote,
                                "avg_entry_ref": avg_entry_ref,
                                "cost_basis_closed": cost_basis_closed,
                                "reference_ts": reference_ts,
                                "meta_complete": bool(meta_complete)
                                if meta_complete is not None
                                else False,
                            }
                        )
                    )

                cur.execute("SELECT payload FROM journal ORDER BY ts")
                journal_rows = [row[0] for row in cur.fetchall() if isinstance(row[0], dict)]
                runtime.journal = DecisionJournal()
                runtime.journal.load_dicts(journal_rows)

                cur.execute(
                    """
                    SELECT ts, equity, cash, positions, pnl, drawdown_pct
                    FROM equity_points
                    ORDER BY ts
                    """
                )
                eq_rows = []
                for ts, eq, cash, positions, pnl, drawdown in cur.fetchall():
                    item: dict[str, Any] = {
                        "t": (ts.isoformat() if isinstance(ts, datetime) else str(ts)),
                        "equity": float(eq),
                    }
                    if cash is not None:
                        item["cash"] = float(cash)
                    if positions is not None:
                        item["positions"] = int(positions)
                    if pnl is not None:
                        item["pnl"] = float(pnl)
                    if drawdown is not None:
                        item["drawdown_pct"] = float(drawdown)
                    eq_rows.append(item)
                session_iso = (
                    session_started_at.isoformat()
                    if isinstance(session_started_at, datetime)
                    else str(session_started_at)
                )
                runtime.equity_history.restore(eq_rows, session_started_at=session_iso)

                cur.execute(
                    "SELECT last_buy_at, signal_history FROM runtime_meta WHERE id = %s",
                    (_SINGLETON_ID,),
                )
                meta = cur.fetchone()
                if meta:
                    last_buy_raw, signal_history = meta
                    runtime._last_buy_at = parse_last_buys(
                        last_buy_raw if isinstance(last_buy_raw, dict) else {}
                    )
                    if isinstance(signal_history, list):
                        runtime.signal_history = [
                            row for row in signal_history if isinstance(row, dict)
                        ][-100:]

        return True

    def save_runtime(self, runtime: TradingRuntime) -> None:
        def _once() -> None:
            with get_conn() as conn:
                try:
                    with conn.cursor() as cur:
                        _lock_paper_book(cur)
                        self._upsert_account(cur, runtime)
                        self._replace_positions(cur, runtime)
                        self._replace_trades(cur, runtime)
                        self._replace_journal(cur, runtime)
                        self._replace_equity_points(cur, runtime)
                        self._upsert_runtime_meta(cur, runtime)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

        _with_deadlock_retry("save_runtime", _once)

    def save_equity_only(self, runtime: TradingRuntime) -> None:
        """Flush the equity ring buffer without rewriting the whole book."""

        def _once() -> None:
            with get_conn() as conn:
                try:
                    with conn.cursor() as cur:
                        _lock_paper_book(cur)
                        # Lock account first (same order as save_runtime / set_kill).
                        session_started = runtime.equity_history.session_started_at
                        cur.execute(
                            """
                            UPDATE account
                            SET session_started_at = %s, updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                            """,
                            (session_started, _SINGLETON_ID),
                        )
                        self._replace_equity_points(cur, runtime)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

        _with_deadlock_retry("save_equity_only", _once)

    def save_params(
        self,
        params: BotParams,
        alerts: AlertParams,
        llm: LlmSettings | None = None,
    ) -> None:
        llm_settings = llm if llm is not None else LlmSettings()
        with get_conn() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO bot_params (id, params, alerts, llm, updated_at)
                        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (id) DO UPDATE SET
                          params = EXCLUDED.params,
                          alerts = EXCLUDED.alerts,
                          llm = EXCLUDED.llm,
                          updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            _SINGLETON_ID,
                            Json(params.model_dump()),
                            Json(alerts.model_dump()),
                            Json(llm_settings.model_dump()),
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def request_reset(self, initial_balance: float | None) -> None:
        """Ask the lease owner to reset the paper book. Does not touch positions or trades."""

        def _once() -> None:
            with get_conn() as conn:
                try:
                    with conn.cursor() as cur:
                        _lock_paper_book(cur)
                        cur.execute(
                            """
                            INSERT INTO runtime_meta (id, reset_requested, reset_balance, updated_at)
                            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                            ON CONFLICT (id) DO UPDATE SET
                              reset_requested = EXCLUDED.reset_requested,
                              reset_balance = EXCLUDED.reset_balance,
                              updated_at = CURRENT_TIMESTAMP
                            """,
                            (_SINGLETON_ID, True, initial_balance),
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

        _with_deadlock_retry("request_reset", _once)

    def peek_reset_request(self) -> tuple[bool, float | None]:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT reset_requested, reset_balance
                    FROM runtime_meta WHERE id = %s
                    """,
                    (_SINGLETON_ID,),
                )
                row = cur.fetchone()
        if row is None:
            return False, None
        requested, balance = row
        parsed = float(balance) if balance is not None else None
        return bool(requested), parsed

    def clear_reset_request(self) -> None:
        def _once() -> None:
            with get_conn() as conn:
                try:
                    with conn.cursor() as cur:
                        _lock_paper_book(cur)
                        cur.execute(
                            """
                            UPDATE runtime_meta
                            SET reset_requested=%s, reset_balance=NULL, updated_at=CURRENT_TIMESTAMP
                            WHERE id=%s
                            """,
                            (False, _SINGLETON_ID),
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

        _with_deadlock_retry("clear_reset_request", _once)

    @staticmethod
    def _parse_close_requests(raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                return []
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for item in raw:
            if isinstance(item, str) and item.strip():
                sym = item.strip().upper()
                if sym not in out:
                    out.append(sym)
            elif isinstance(item, dict):
                sym = str(item.get("symbol") or "").strip().upper()
                if sym and sym not in out:
                    out.append(sym)
        return out

    def enqueue_close(self, symbol: str) -> list[str]:
        """Ask the lease owner to fully close one paper position."""
        sym = symbol.strip().upper()
        if not sym:
            return self.peek_close_requests()

        def _once() -> list[str]:
            with get_conn() as conn:
                try:
                    with conn.cursor() as cur:
                        _lock_paper_book(cur)
                        cur.execute(
                            "SELECT close_requests FROM runtime_meta WHERE id = %s",
                            (_SINGLETON_ID,),
                        )
                        row = cur.fetchone()
                        pending = self._parse_close_requests(row[0] if row else None)
                        if sym not in pending:
                            pending.append(sym)
                        cur.execute(
                            """
                            INSERT INTO runtime_meta (id, close_requests, updated_at)
                            VALUES (%s, %s, CURRENT_TIMESTAMP)
                            ON CONFLICT (id) DO UPDATE SET
                              close_requests = EXCLUDED.close_requests,
                              updated_at = CURRENT_TIMESTAMP
                            """,
                            (_SINGLETON_ID, Json(pending)),
                        )
                    conn.commit()
                    return pending
                except Exception:
                    conn.rollback()
                    raise

        return _with_deadlock_retry("enqueue_close", _once)

    def peek_close_requests(self) -> list[str]:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT close_requests FROM runtime_meta WHERE id = %s",
                    (_SINGLETON_ID,),
                )
                row = cur.fetchone()
        return self._parse_close_requests(row[0] if row else None)

    def clear_close_requests(self, symbols: list[str] | None = None) -> None:
        def _once() -> None:
            with get_conn() as conn:
                try:
                    with conn.cursor() as cur:
                        _lock_paper_book(cur)
                        if symbols is None:
                            remaining: list[str] = []
                        else:
                            drop = {s.strip().upper() for s in symbols if s.strip()}
                            cur.execute(
                                "SELECT close_requests FROM runtime_meta WHERE id = %s",
                                (_SINGLETON_ID,),
                            )
                            row = cur.fetchone()
                            remaining = [
                                s
                                for s in self._parse_close_requests(row[0] if row else None)
                                if s not in drop
                            ]
                        cur.execute(
                            """
                            UPDATE runtime_meta
                            SET close_requests=%s, updated_at=CURRENT_TIMESTAMP
                            WHERE id=%s
                            """,
                            (Json(remaining), _SINGLETON_ID),
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

        _with_deadlock_retry("clear_close_requests", _once)

    def set_kill(self, killed: bool, kill_reason: str | None) -> bool:
        """Update kill flags only. Returns False when the account row does not exist."""

        def _once() -> bool:
            with get_conn() as conn:
                try:
                    with conn.cursor() as cur:
                        _lock_paper_book(cur)
                        cur.execute(
                            """
                            UPDATE account
                            SET killed=%s, kill_reason=%s, updated_at=CURRENT_TIMESTAMP
                            WHERE id=%s
                            """,
                            (killed, kill_reason, _SINGLETON_ID),
                        )
                        cur.execute(
                            "SELECT killed FROM account WHERE id=%s",
                            (_SINGLETON_ID,),
                        )
                        row = cur.fetchone()
                    if row is None:
                        conn.rollback()
                        return False
                    conn.commit()
                    return True
                except Exception:
                    conn.rollback()
                    raise

        return _with_deadlock_retry("set_kill", _once)

    def load_params(self) -> tuple[BotParams, AlertParams, LlmSettings]:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT params, alerts, llm FROM bot_params WHERE id = %s",
                    (_SINGLETON_ID,),
                )
                row = cur.fetchone()
        if not row:
            return BotParams(), AlertParams(), LlmSettings()
        params_raw = row[0]
        alerts_raw = row[1]
        llm_raw = row[2] if len(row) > 2 else {}
        params = BotParams.model_validate(params_raw if isinstance(params_raw, dict) else {})
        alerts = AlertParams.model_validate(alerts_raw if isinstance(alerts_raw, dict) else {})
        llm = LlmSettings.model_validate(llm_raw if isinstance(llm_raw, dict) else {})
        return params, alerts, llm

    def audit(self, actor: str, action: str, payload: dict[str, Any] | None = None) -> None:
        with get_conn() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO control_audit (ts, actor, action, payload)
                        VALUES (CURRENT_TIMESTAMP, %s, %s, %s)
                        """,
                        (actor, action, Json(payload or {})),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _upsert_account(self, cur, runtime: TradingRuntime) -> None:
        session_started = runtime.equity_history.session_started_at
        cur.execute(
            """
            INSERT INTO account (
              id, mode, cash, initial_balance, realized_pnl, peak_equity,
              killed, kill_reason, session_started_at, updated_at
            ) VALUES (
              %s, 'paper', %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP
            )
            ON CONFLICT (id) DO UPDATE SET
              cash = EXCLUDED.cash,
              initial_balance = EXCLUDED.initial_balance,
              realized_pnl = EXCLUDED.realized_pnl,
              peak_equity = EXCLUDED.peak_equity,
              killed = EXCLUDED.killed,
              kill_reason = EXCLUDED.kill_reason,
              session_started_at = EXCLUDED.session_started_at,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                _SINGLETON_ID,
                float(runtime.wallet.cash),
                float(runtime.wallet.initial_balance),
                float(runtime.wallet.realized_pnl),
                float(runtime.peak_equity),
                bool(runtime.killed),
                runtime.kill_reason,
                session_started,
            ),
        )

    def _replace_positions(self, cur, runtime: TradingRuntime) -> None:
        cur.execute("DELETE FROM positions")
        for sym, pos in runtime.wallet.positions.items():
            levels = runtime._exit_levels.get(sym, {})
            stop_pct = levels.get("stop_pct")
            take_pct = levels.get("take_pct")
            exit_meta = _exit_meta_from_levels(levels)
            cur.execute(
                """
                INSERT INTO positions (
                  symbol, qty, avg_entry, stop_pct, take_pct, exit_meta, cost_basis, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                (
                    sym,
                    float(pos.qty),
                    float(pos.avg_entry),
                    float(stop_pct) if stop_pct is not None else None,
                    float(take_pct) if take_pct is not None else None,
                    Json(exit_meta),
                    float(pos.cost_basis),
                ),
            )

    def _replace_trades(self, cur, runtime: TradingRuntime) -> None:
        cur.execute("DELETE FROM trades")
        for trade in runtime.wallet.trades:
            cur.execute(
                """
                INSERT INTO trades (
                  id, symbol, side, qty, price, fee, ts, realized_pnl, note,
                  decision_ts, decision_price, latency_ms, slip_bps, impact_bps,
                  invested_quote, avg_entry_ref, cost_basis_closed, reference_ts, meta_complete
                )
                VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s
                )
                """,
                (
                    trade.id,
                    trade.symbol,
                    trade.side,
                    float(trade.qty),
                    float(trade.price),
                    float(trade.fee),
                    trade.ts,
                    float(trade.realized_pnl),
                    trade.note,
                    trade.decision_ts,
                    trade.decision_price,
                    trade.latency_ms,
                    trade.slip_bps,
                    trade.impact_bps,
                    trade.invested_quote,
                    trade.avg_entry_ref,
                    trade.cost_basis_closed,
                    trade.reference_ts,
                    bool(trade.meta_complete),
                ),
            )

    def _replace_journal(self, cur, runtime: TradingRuntime) -> None:
        cur.execute("DELETE FROM journal")
        for entry in runtime.journal.entries:
            cur.execute(
                """
                INSERT INTO journal (id, ts, payload)
                VALUES (%s, %s, %s)
                """,
                (entry.id, entry.ts, Json(entry.to_dict())),
            )

    def _replace_equity_points(self, cur, runtime: TradingRuntime) -> None:
        cur.execute("DELETE FROM equity_points")
        for point in runtime.equity_history.dump():
            if not isinstance(point, dict):
                continue
            try:
                ts = _parse_ts(str(point.get("t")))
                equity = float(point["equity"])
                cash = float(point["cash"]) if point.get("cash") is not None else None
                positions = int(point["positions"]) if point.get("positions") is not None else None
                pnl = float(point["pnl"]) if point.get("pnl") is not None else None
                drawdown = (
                    float(point["drawdown_pct"]) if point.get("drawdown_pct") is not None else None
                )
            except (KeyError, TypeError, ValueError):
                continue
            cur.execute(
                """
                INSERT INTO equity_points (ts, equity, cash, positions, pnl, drawdown_pct)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (ts) DO UPDATE SET
                  equity = EXCLUDED.equity,
                  cash = EXCLUDED.cash,
                  positions = EXCLUDED.positions,
                  pnl = EXCLUDED.pnl,
                  drawdown_pct = EXCLUDED.drawdown_pct
                """,
                (ts, equity, cash, positions, pnl, drawdown),
            )

    def _upsert_runtime_meta(self, cur, runtime: TradingRuntime) -> None:
        last_buy = {k: v.isoformat() for k, v in runtime._last_buy_at.items()}
        # Omit reset_requested / reset_balance so a tick cannot clear an API reset.
        cur.execute(
            """
            INSERT INTO runtime_meta (id, last_buy_at, signal_history, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO UPDATE SET
              last_buy_at = EXCLUDED.last_buy_at,
              signal_history = EXCLUDED.signal_history,
              updated_at = CURRENT_TIMESTAMP
            """,
            (_SINGLETON_ID, Json(last_buy), Json(runtime.signal_history[-50:])),
        )

    def _apply_paper_json(self, cur, data: dict[str, Any], *, mark_migrated: bool) -> None:
        wallet_data = data.get("wallet") if isinstance(data.get("wallet"), dict) else {}
        initial_balance = float(wallet_data.get("initial_balance") or 10_000.0)
        cash = float(wallet_data.get("cash") if wallet_data.get("cash") is not None else initial_balance)
        realized_pnl = float(wallet_data.get("realized_pnl") or 0.0)
        peak_equity = float(data.get("peak_equity") if data.get("peak_equity") is not None else cash)
        killed = bool(data.get("killed"))
        kill_reason = data.get("kill_reason")
        session_raw = data.get("session_started_at")
        session_started = _parse_ts(str(session_raw)) if session_raw else _utc_now()

        cur.execute(
            """
            INSERT INTO account (
              id, mode, cash, initial_balance, realized_pnl, peak_equity,
              killed, kill_reason, session_started_at, updated_at
            ) VALUES (%s, 'paper', %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """,
            (
                _SINGLETON_ID,
                cash,
                initial_balance,
                realized_pnl,
                peak_equity,
                killed,
                str(kill_reason) if kill_reason else None,
                session_started,
            ),
        )

        cur.execute("DELETE FROM positions")
        for row in wallet_data.get("positions") or []:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "")
            qty = float(row.get("qty") or 0)
            if not sym or qty <= 0:
                continue
            avg_entry = float(row.get("avg_entry") or 0)
            cost_basis = row.get("cost_basis")
            basis = float(cost_basis) if cost_basis is not None else avg_entry * qty
            cur.execute(
                """
                INSERT INTO positions (
                  symbol, qty, avg_entry, stop_pct, take_pct, exit_meta, cost_basis, updated_at
                )
                VALUES (%s, %s, %s, NULL, NULL, %s, %s, CURRENT_TIMESTAMP)
                """,
                (sym, qty, avg_entry, Json({}), basis),
            )

        cur.execute("DELETE FROM trades")
        for row in wallet_data.get("trades") or []:
            if not isinstance(row, dict):
                continue
            trade_id = str(row.get("id") or "")
            if not trade_id:
                continue
            trade = Trade.from_dict(row)
            cur.execute(
                """
                INSERT INTO trades (
                  id, symbol, side, qty, price, fee, ts, realized_pnl, note,
                  decision_ts, decision_price, latency_ms, slip_bps, impact_bps,
                  invested_quote, avg_entry_ref, cost_basis_closed, reference_ts, meta_complete
                )
                VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s
                )
                """,
                (
                    trade_id,
                    trade.symbol,
                    trade.side,
                    float(trade.qty),
                    float(trade.price),
                    float(trade.fee),
                    trade.ts,
                    float(trade.realized_pnl),
                    trade.note,
                    trade.decision_ts,
                    trade.decision_price,
                    trade.latency_ms,
                    trade.slip_bps,
                    trade.impact_bps,
                    trade.invested_quote,
                    trade.avg_entry_ref,
                    trade.cost_basis_closed,
                    trade.reference_ts,
                    bool(trade.meta_complete),
                ),
            )

        cur.execute("DELETE FROM journal")
        for row in data.get("journal") or []:
            if not isinstance(row, dict):
                continue
            entry_id = str(row.get("id") or "")
            if not entry_id:
                continue
            cur.execute(
                "INSERT INTO journal (id, ts, payload) VALUES (%s, %s, %s)",
                (entry_id, _parse_ts(str(row.get("ts")) if row.get("ts") else None), Json(row)),
            )

        cur.execute("DELETE FROM equity_points")
        for row in data.get("equity_history") or []:
            if not isinstance(row, dict):
                continue
            try:
                ts = _parse_ts(str(row.get("t")))
                equity = float(row["equity"])
            except (KeyError, TypeError, ValueError):
                continue
            cur.execute(
                "INSERT INTO equity_points (ts, equity) VALUES (%s, %s) ON CONFLICT (ts) DO NOTHING",
                (ts, equity),
            )

        last_buy = data.get("last_buy_at") if isinstance(data.get("last_buy_at"), dict) else {}
        signal_history = data.get("signal_history") if isinstance(data.get("signal_history"), list) else []
        migrated_at = _utc_now() if mark_migrated else None
        cur.execute(
            """
            INSERT INTO runtime_meta (id, last_buy_at, signal_history, migrated_at, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO UPDATE SET
              last_buy_at = EXCLUDED.last_buy_at,
              signal_history = EXCLUDED.signal_history,
              migrated_at = COALESCE(runtime_meta.migrated_at, EXCLUDED.migrated_at),
              updated_at = CURRENT_TIMESTAMP
            """,
            (_SINGLETON_ID, Json(last_buy), Json(signal_history[-50:]), migrated_at),
        )
