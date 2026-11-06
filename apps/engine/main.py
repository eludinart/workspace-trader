"""FastAPI entrypoint — paper trading engine."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from app import store as store_module
from app.backtest import run_backtest
from app.config import BotParams
from app.control_auth import mask_secret_values, require_control_read, require_control_write
from app.db import get_conn
from app.lease import read_lease
from app.migrate import run_migrations
from app.news.models import NewsCategory
from app.runtime import LeaseLost, TradingRuntime
from app.signals.confluence import score_confluence
from app.signals.kelly import evaluate_kelly
from app.signals.mtf import evaluate_mtf_gate
from app.signals.onchain import stub_onchain
from app.signals.oos import evaluate_oos
from app.signals.passive_wake import evaluate_passive_wake
from app.signals.providers import list_provider_specs
from app.signals.risk_caps import HARD_RISK_PER_TRADE_PCT
from app.signals.super_opp import detect_super_opportunity
from app.store import Store
from app.wallet import annotate_trade_for_display

_LAST_BACKTEST_PATH = Path(__file__).resolve().parent / "data" / "last_backtest.json"
_BACKTEST_SUMMARY_KEYS = (
    "ok",
    "error",
    "symbol",
    "start",
    "end",
    "candle_count",
    "initial_balance",
    "cash",
    "final_equity",
    "total_pnl",
    "realized_pnl",
    "unrealized_pnl",
    "trade_count",
    "closed_roundtrips",
    "win_rate",
    "wins",
    "losses",
    "max_drawdown",
    "positions",
    "trading_style",
    "buy_threshold",
    "take_profit_pct",
    "min_hold_min",
)


def _load_last_backtest() -> dict[str, Any] | None:
    try:
        if not _LAST_BACKTEST_PATH.exists():
            return None
        data = json.loads(_LAST_BACKTEST_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _save_last_backtest(result: dict[str, Any], params: BotParams) -> dict[str, Any]:
    summary = {k: result[k] for k in _BACKTEST_SUMMARY_KEYS if k in result}
    summary.setdefault("trading_style", params.trading_style)
    summary.setdefault("buy_threshold", params.buy_threshold)
    summary.setdefault("take_profit_pct", params.take_profit_pct)
    summary.setdefault("min_hold_min", params.min_hold_min)
    try:
        _LAST_BACKTEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LAST_BACKTEST_PATH.write_text(
            json.dumps(summary, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError:
        logger.exception("failed to persist last backtest")
    return summary

_NEWS_CATEGORIES: dict[str, NewsCategory] = {
    "crypto": "crypto",
    "macro": "macro",
    "tech": "tech",
    "world": "world",
}

logger = logging.getLogger(__name__)
runtime = TradingRuntime(store=Store())


def _health_db_lease() -> tuple[dict[str, Any], dict[str, Any]]:
    empty_lease: dict[str, Any] = {
        "ok": False,
        "held": False,
        "owner_id": None,
        "heartbeat_at": None,
        "expires_at": None,
        "release_requested": False,
        "version": None,
    }
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}, {**empty_lease, "error": "database unavailable"}
    try:
        return {"ok": True}, read_lease()
    except Exception as exc:  # noqa: BLE001
        return {"ok": True}, {**empty_lease, "error": str(exc)}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # HTTP API only: schema + read model. Trade loops live in the trader worker.
    try:
        run_migrations()
        if runtime.store is not None:
            runtime.store.maybe_migrate_json()
        runtime.hydrate()
    except Exception:
        logger.exception("API store init failed; /health will report database down")
        runtime._db_ok = False
    yield
    await runtime.shutdown_streams()
    runtime.close()


app = FastAPI(
    title="workspace-trader-engine",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def refresh_api_read_model(request: Request, call_next):
    if request.method == "GET" and runtime.lease is None and runtime.store is not None:
        try:
            await asyncio.to_thread(runtime.hydrate)
        except Exception:
            logger.warning("read-model refresh failed", exc_info=True)
            runtime._db_ok = False
    return await call_next(request)


class ParamsUpdate(BaseModel):
    symbol: str | None = None
    trading_style: str | None = None
    initial_balance: float | None = Field(default=None, gt=0)
    position_size_pct: float | None = Field(default=None, gt=0, le=1.0)
    decision_interval_sec: int | None = Field(default=None, ge=10, le=3600)
    rsi_period: int | None = None
    rsi_oversold: float | None = None
    rsi_overbought: float | None = None
    macd_fast: int | None = None
    macd_slow: int | None = None
    macd_signal: int | None = None
    volume_ma_period: int | None = None
    buy_threshold: float | None = None
    sell_threshold: float | None = None
    candle_granularity_sec: int | None = None
    candle_lookback: int | None = None
    auto_trade: bool | None = None
    fee_bps: float | None = None
    slippage_bps: float | None = None
    latency_ms: int | None = Field(default=None, ge=0, le=5000)
    latency_wall_clock: bool | None = None
    order_impact_enabled: bool | None = None
    impact_bps_max: float | None = Field(default=None, ge=0, le=50)
    order_book_depth_enabled: bool | None = None
    order_book_depth_levels: int | None = Field(default=None, ge=1, le=20)
    confluence_enabled: bool | None = None
    mtf_enabled: bool | None = None
    kelly_enabled: bool | None = None
    onchain_enabled: bool | None = None
    super_opp_enabled: bool | None = None
    passive_wake_enabled: bool | None = None
    post_trade_ai_enabled: bool | None = None
    web_push_enabled: bool | None = None
    kill_switch_enabled: bool | None = None
    max_daily_loss_pct: float | None = None
    max_drawdown_pct: float | None = None
    symbols: list[str] | str | None = None
    watchlist: list[str] | str | None = None
    take_profit_pct: float | None = Field(default=None, ge=0)
    stop_loss_pct: float | None = Field(default=None, ge=0)
    max_position_pct: float | None = Field(default=None, gt=0)
    add_cooldown_min: int | None = Field(default=None, ge=0, le=24 * 60)
    max_open_symbols: int | None = Field(default=None, ge=1, le=30)
    capital_per_slot: float | None = Field(default=None, ge=20, le=500)
    max_open_hard_cap: int | None = Field(default=None, ge=5, le=50)
    max_portfolio_risk_r: float | None = Field(default=None, ge=1, le=30)
    universe_enabled: bool | None = None
    universe_size: int | None = Field(default=None, ge=10, le=80)
    universe_min_volume_usd: float | None = Field(default=None, ge=0)
    universe_refresh_hours: float | None = Field(default=None, ge=1, le=48)
    risk_per_trade_pct: float | None = Field(default=None, gt=0, le=0.02)
    atr_stop_mult: float | None = Field(default=None, ge=1, le=4)
    reward_risk: float | None = Field(default=None, ge=1, le=5)
    min_hold_min: int | None = Field(default=None, ge=0, le=24 * 60)
    aggressive_sell_confidence: float | None = Field(default=None, ge=0.5, le=1.0)


class AlertUpdate(BaseModel):
    telegram_enabled: bool | None = None
    telegram_chat_id: str | None = None
    telegram_bot_token: str | None = None
    email_enabled: bool | None = None
    email_to: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    alert_on_trade: bool | None = None
    alert_on_pnl: bool | None = None
    alert_on_strong_signal: bool | None = None
    pnl_alert_threshold: float | None = None
    strong_signal_threshold: float | None = None


class ResetBody(BaseModel):
    initial_balance: float | None = Field(default=None, gt=0)


class KillBody(BaseModel):
    reason: str = "manual"


class ClosePositionBody(BaseModel):
    symbol: str = Field(min_length=3, max_length=32)


class BacktestBody(BaseModel):
    symbol: str = "BTC-USD"
    start: datetime
    end: datetime
    initial_balance: float | None = Field(default=None, gt=0)
    position_size_pct: float | None = Field(default=None, gt=0, le=1.0)
    candle_granularity_sec: int | None = None


def _mask_alerts() -> dict[str, Any]:
    out = runtime.alerts.params.model_dump()
    if out.get("telegram_bot_token"):
        out["telegram_bot_token"] = "***"
    if out.get("smtp_password"):
        out["smtp_password"] = "***"
    return out


@app.get("/health")
def health() -> dict[str, Any]:
    db_status, lease_status = _health_db_lease()
    if db_status.get("ok"):
        try:
            runtime.hydrate()
        except Exception as exc:  # noqa: BLE001
            logger.warning("health hydrate failed: %s", exc)
            runtime._db_ok = False
    body: dict[str, Any] = {
        "status": "ok" if db_status.get("ok") else "degraded",
        "service": "engine",
        "mode": "paper",
        "role": "api",
        "trade_loops": False,
        "db": db_status,
        "lease": lease_status,
    }
    try:
        body.update(runtime.status())
    except Exception as exc:  # noqa: BLE001
        body["runtime_error"] = str(exc)
    body["db"] = db_status
    body["lease"] = lease_status
    body["role"] = "api"
    body["trade_loops"] = bool(runtime.running)
    return body


@app.get("/system/status")
def system_status() -> dict[str, Any]:
    """Dashboard board: which features are actually active / healthy."""
    return runtime.system_status()


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "workspace-trader engine", "mode": "paper", "docs": "/docs"}


@app.get("/account")
def get_account() -> dict[str, Any]:
    return runtime.account_state()


@app.get("/account/equity-history")
def get_equity_history(range: str = "1h") -> dict[str, Any]:
    """Paper equity curve. range=1h|6h|24h|1d|1w|1mo|1y|session|all"""
    allowed = {"1h", "6h", "24h", "1d", "1w", "1mo", "1y", "session", "all"}
    key = range if range in allowed else "1h"
    return runtime.equity_curve(key)


@app.get("/positions")
def get_positions() -> dict[str, Any]:
    state = runtime.account_state()
    return {"positions": state["positions"], "cash": state["cash"]}


@app.post("/positions/close")
def close_position(body: ClosePositionBody) -> dict[str, Any]:
    """Fully sell one open paper position (manual). Queued for the lease owner when split."""
    try:
        result = runtime.close_position(body.symbol)
    except LeaseLost as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason") or "close failed")
    return result


@app.get("/trades")
def get_trades(limit: int = 50) -> dict[str, Any]:
    trades = [
        annotate_trade_for_display(t) for t in reversed(runtime.wallet.trades[-limit:])
    ]
    return {"trades": trades}


@app.get("/signals/flags")
def get_signal_flags() -> dict[str, Any]:
    return {"flags": runtime.signal_flags().as_dict()}


@app.get("/signals/oos")
def get_oos_harness(min_holdout: int = 30) -> dict[str, Any]:
    pnls = [
        float(t.realized_pnl)
        for t in runtime.wallet.trades
        if t.side == "sell" and t.meta_complete
    ]
    report = evaluate_oos(pnls, min_holdout=min_holdout)
    return report.to_dict()


@app.get("/signals/stubs")
def get_signal_stubs(symbol: str | None = None) -> dict[str, Any]:
    """Read-only status of points 14–21 stubs (all gated by flags, off by default)."""
    flags = runtime.signal_flags()
    sym = (symbol or runtime.params.symbol or "BTC-USD").upper()
    pnls = [
        float(t.realized_pnl)
        for t in runtime.wallet.trades
        if t.side == "sell" and getattr(t, "meta_complete", False)
    ]
    oos = evaluate_oos(pnls)
    kelly = evaluate_kelly(
        pnls,
        enabled=flags.kelly_enabled,
        oos_activation_allowed=bool(oos.activation_allowed),
        allow_sizing=False,
    )
    return {
        "flags": flags.as_dict(),
        "hard_risk_per_trade_pct": HARD_RISK_PER_TRADE_PCT,
        "oos": oos.to_dict(),
        "confluence": score_confluence({}, enabled=flags.confluence_enabled).to_dict(),
        "mtf": evaluate_mtf_gate(
            enabled=flags.mtf_enabled, intended_side="buy"
        ).to_dict(),
        "kelly": kelly.to_dict(),
        "onchain": {
            k: stub_onchain(k, sym, enabled=flags.onchain_enabled).to_dict()  # type: ignore[arg-type]
            for k in ("whales", "funding", "liquidations")
        },
        "providers": list_provider_specs(),
        "super_opp": detect_super_opportunity(
            enabled=flags.super_opp_enabled,
            risk_per_trade_pct=float(runtime.params.risk_per_trade_pct),
        ).to_dict(),
        "passive_wake": evaluate_passive_wake(
            enabled=flags.passive_wake_enabled, move_pct=None
        ).to_dict(),
        "post_trade_ai": {
            "enabled": flags.post_trade_ai_enabled,
            "note": "async journal-only; never mutates PnL/positions; off in backtest pretrade",
        },
        "web_push": {
            "enabled": flags.web_push_enabled,
            "note": "VAPID via env only; no Telegram; HTTPS domain required",
        },
        "order_book_depth": {
            "enabled": bool(getattr(runtime.params, "order_book_depth_enabled", False)),
            "levels": int(getattr(runtime.params, "order_book_depth_levels", 5) or 5),
            "label": "simulated_book:hypothesis",
        },
    }


@app.get("/pnl")
def get_pnl() -> dict[str, Any]:
    state = runtime.account_state()
    return {
        "initial_balance": state["initial_balance"],
        "equity": state["equity"],
        "cash": state["cash"],
        "realized_pnl": state["realized_pnl"],
        "unrealized_pnl": state["unrealized_pnl"],
        "total_pnl": state["total_pnl"],
        "drawdown_pct": state["drawdown_pct"],
        "risk_score": state["risk_score"],
    }


@app.get("/market/price")
def get_price(symbol: str | None = None) -> dict[str, Any]:
    sym = (symbol or runtime.params.symbol).strip().upper()
    try:
        return runtime.market.get_ticker(sym)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/market/candles")
def get_candles(limit: int | None = None, symbol: str | None = None) -> dict[str, Any]:
    lim = limit or runtime.params.candle_lookback
    sym = (symbol or runtime.params.symbol).strip().upper()
    try:
        candles = runtime.market.candles_as_dicts(
            sym,
            granularity=runtime.params.candle_granularity_sec,
            limit=lim,
        )
        return {"symbol": sym, "candles": candles}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/signals")
def get_signals() -> dict[str, Any]:
    try:
        runtime.news.refresh_if_stale()
    except Exception as exc:  # noqa: BLE001
        logger.warning("news refresh: %s", exc)
    payload = runtime.signal_view()
    payload["news"] = runtime.news.brief_dict()
    return payload


@app.get("/news")
def get_news(category: str | None = None, limit: int = 40) -> dict[str, Any]:
    selected: NewsCategory | None = None
    if category is not None:
        selected = _NEWS_CATEGORIES.get(category)
        if selected is None:
            raise HTTPException(
                status_code=400,
                detail="category must be crypto, macro, tech, or world",
            )
    bounded = max(1, min(limit, 100))
    try:
        return runtime.news.items_payload(category=selected, limit=bounded)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/news/brief")
def get_news_brief() -> dict[str, Any]:
    try:
        return runtime.news.brief_payload(refresh=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/journal")
def get_journal(limit: int = 50, source: str | None = None) -> dict[str, Any]:
    return {"entries": runtime.journal.list(limit=limit, source=source)}


@app.get("/params")
def get_params() -> BotParams:
    return runtime.params


@app.put("/params")
def put_params(body: ParamsUpdate) -> BotParams:
    return runtime.update_params(body.model_dump(exclude_unset=True))


@app.get("/universe")
def get_universe() -> dict[str, Any]:
    return runtime.universe_view()


@app.post("/universe/refresh")
def refresh_universe() -> dict[str, Any]:
    runtime._ensure_universe(force=True)
    return runtime.universe_view()


@app.get("/alerts/params")
def get_alert_params() -> dict[str, Any]:
    return _mask_alerts()


@app.put("/alerts/params")
def put_alert_params(body: AlertUpdate) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    if patch.get("telegram_bot_token") == "***":
        patch.pop("telegram_bot_token")
    if patch.get("smtp_password") == "***":
        patch.pop("smtp_password")
    runtime.update_alerts(patch)
    return _mask_alerts()


@app.get("/alerts/history")
def get_alert_history() -> dict[str, Any]:
    return {"alerts": list(reversed(runtime.alerts.history[-30:]))}


@app.get("/backtest/last")
def get_last_backtest() -> dict[str, Any]:
    last = _load_last_backtest()
    if not last:
        return {"ok": False, "error": "no_backtest"}
    return last


@app.post("/backtest")
def post_backtest(body: BacktestBody) -> dict[str, Any]:
    params = runtime.params.model_copy(deep=True)
    updates: dict[str, Any] = {"symbol": body.symbol}
    if body.initial_balance is not None:
        updates["initial_balance"] = body.initial_balance
    if body.position_size_pct is not None:
        updates["position_size_pct"] = body.position_size_pct
    if body.candle_granularity_sec is not None:
        updates["candle_granularity_sec"] = body.candle_granularity_sec
    # Align paper wallet capital when caller does not override.
    if body.initial_balance is None:
        updates["initial_balance"] = float(runtime.wallet.initial_balance)
    params = params.model_copy(update=updates)
    try:
        result = run_backtest(
            runtime.market,
            symbol=body.symbol,
            start=body.start,
            end=body.end,
            params=params,
        )
        if result.get("ok"):
            _save_last_backtest(result, params)
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/account/reset")
def reset_account(body: ResetBody | None = None) -> dict[str, Any]:
    """Accept a reset. The API flags it; the trader applies it under the lease."""
    bal = body.initial_balance if body else None
    return runtime.reset_account(bal)


@app.post("/bot/start")
async def bot_start() -> dict[str, Any]:
    await runtime.start()
    return runtime.status()


@app.post("/bot/stop")
async def bot_stop() -> dict[str, Any]:
    await runtime.stop()
    return runtime.status()


@app.post("/bot/kill")
def bot_kill(body: KillBody | None = None) -> dict[str, Any]:
    reason = body.reason if body else "manual"
    return runtime.engage_kill_switch(reason)


@app.post("/bot/resume")
def bot_resume() -> dict[str, Any]:
    return runtime.resume_from_kill()


@app.post("/bot/tick")
async def bot_tick() -> dict[str, Any]:
    try:
        return await runtime.tick()
    except LeaseLost as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _control_read_failed(exc: Exception) -> HTTPException:
    logger.exception("control read failed")
    return HTTPException(status_code=503, detail="control read failed")


def _control_write_failed(exc: Exception) -> HTTPException:
    logger.exception("control write failed")
    return HTTPException(status_code=503, detail="control write failed")


def _fetch_account() -> tuple[Any, ...] | None:
    """Return ``(mode, cash, killed, kill_reason)`` or None when the row is missing."""
    try:
        with store_module.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT mode, cash, killed, kill_reason
                    FROM account WHERE id = %s
                    """,
                    (1,),
                )
                return cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        raise _control_read_failed(exc) from exc


def _assert_paper_mode() -> None:
    row = _fetch_account()
    if row is None:
        return
    if str(row[0]) != "paper":
        raise HTTPException(status_code=403, detail="paper mode required")


def _audit(actor: str, action: str, payload: dict[str, Any]) -> None:
    if runtime.store is None:
        raise HTTPException(status_code=503, detail="control write failed")
    try:
        runtime.store.audit(actor, action, payload)
    except Exception as exc:  # noqa: BLE001
        raise _control_write_failed(exc) from exc


def _public_params() -> dict[str, Any]:
    return mask_secret_values(runtime.params.model_dump(mode="json"))


@app.get("/control/status")
def control_status(_actor: str = Depends(require_control_read)) -> dict[str, Any]:
    """Paper book, kill flag, and lease. Reads Postgres, not the API process cache."""
    try:
        if runtime.store is not None:
            runtime._reload_params_only()
        row = _fetch_account()
        with store_module.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, qty, avg_entry FROM positions ORDER BY symbol"
                )
                pos_rows = cur.fetchall()
        lease = read_lease()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _control_read_failed(exc) from exc

    if row is None:
        cash = float(runtime.params.initial_balance)
        killed = False
        kill_reason = None
        pos_rows = []
    else:
        mode, cash, killed, kill_reason = row
        if str(mode) != "paper":
            raise HTTPException(status_code=403, detail="paper mode required")
    positions = [
        {"symbol": str(sym), "qty": float(qty), "avg_entry": float(avg)}
        for sym, qty, avg in pos_rows
    ]
    return {
        "mode": "paper",
        "lease": {
            "owner_id": lease.get("owner_id"),
            "heartbeat_at": lease.get("heartbeat_at"),
            "expires_at": lease.get("expires_at"),
            "held": bool(lease.get("held")),
            "release_requested": bool(lease.get("release_requested")),
            "version": lease.get("version"),
        },
        "killed": bool(killed),
        "kill_reason": kill_reason,
        "cash": float(cash),
        "positions": positions,
    }


@app.get("/control/params")
def control_get_params(_actor: str = Depends(require_control_read)) -> dict[str, Any]:
    _assert_paper_mode()
    try:
        if runtime.store is not None:
            runtime._reload_params_only()
    except Exception as exc:  # noqa: BLE001
        raise _control_read_failed(exc) from exc
    return _public_params()


@app.put("/control/params")
def control_put_params(
    body: ParamsUpdate,
    actor: str = Depends(require_control_write),
) -> dict[str, Any]:
    _assert_paper_mode()
    try:
        if runtime.store is not None:
            runtime._reload_params_only()
        before = runtime.params.model_dump(mode="json")
        updated = runtime.update_params(body.model_dump(exclude_unset=True))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=jsonable_encoder(exc.errors())) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _control_write_failed(exc) from exc
    after = updated.model_dump(mode="json")
    diff = {
        key: {"from": before.get(key), "to": value}
        for key, value in after.items()
        if before.get(key) != value
    }
    _audit(actor, "params", {"diff": diff})
    return mask_secret_values(after)


@app.post("/control/kill")
def control_kill(
    body: KillBody | None = None,
    actor: str = Depends(require_control_write),
) -> dict[str, Any]:
    _assert_paper_mode()
    reason = body.reason if body else "manual"
    try:
        runtime.engage_kill_switch(reason)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _control_write_failed(exc) from exc
    _audit(actor, "kill", {"reason": reason, "killed": True})
    return {
        "mode": "paper",
        "killed": True,
        "kill_reason": runtime.kill_reason,
        "auto_trade": runtime.params.auto_trade,
    }


@app.post("/control/resume")
def control_resume(actor: str = Depends(require_control_write)) -> dict[str, Any]:
    _assert_paper_mode()
    try:
        runtime.resume_from_kill()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _control_write_failed(exc) from exc
    _audit(actor, "resume", {"killed": False})
    return {
        "mode": "paper",
        "killed": bool(runtime.killed),
        "kill_reason": runtime.kill_reason,
    }


@app.post("/control/tick")
async def control_tick(actor: str = Depends(require_control_write)) -> dict[str, Any]:
    """Forced paper decision. The API process has no lease, so this stays fail-closed."""
    _assert_paper_mode()
    try:
        result = await runtime.tick()
    except LeaseLost as exc:
        _audit(actor, "tick", {"accepted": False, "detail": "lease"})
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _control_write_failed(exc) from exc
    _audit(actor, "tick", {"accepted": True, "mode": "paper"})
    return {"mode": "paper", "accepted": True, "result": result}


@app.get("/stream")
async def stream(_request: Request) -> StreamingResponse:
    queue = runtime.bus.subscribe()

    async def event_gen():
        try:
            try:
                await asyncio.to_thread(runtime.hydrate)
            except Exception:  # noqa: BLE001
                logger.warning("stream hydrate failed", exc_info=True)
            snapshot = runtime.live_payload("snapshot")
            yield f"data: {json.dumps(snapshot, default=str)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=5)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except TimeoutError:
                    try:
                        await asyncio.to_thread(runtime.hydrate)
                        payload: dict[str, Any] = runtime.live_payload("snapshot")
                    except Exception:  # noqa: BLE001
                        now = datetime.now(timezone.utc).isoformat()
                        payload = {"type": "heartbeat", "ts": now}
                    yield f"data: {json.dumps(payload, default=str)}\n\n"
        finally:
            runtime.bus.unsubscribe(queue)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
