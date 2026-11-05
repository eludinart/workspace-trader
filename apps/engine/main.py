"""FastAPI entrypoint — paper trading engine."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.backtest import run_backtest
from app.config import BotParams
from app.news.models import NewsCategory
from app.runtime import TradingRuntime

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
runtime = TradingRuntime()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await runtime.start()
    yield
    await runtime.stop()
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
    risk_per_trade_pct: float | None = Field(default=None, gt=0, le=0.05)
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
    return {
        "status": "ok",
        "service": "engine",
        "mode": "paper",
        **runtime.status(),
    }


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


@app.get("/trades")
def get_trades(limit: int = 50) -> dict[str, Any]:
    trades = [t.to_dict() for t in reversed(runtime.wallet.trades[-limit:])]
    return {"trades": trades}


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
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/stream")
async def stream(_request: Request) -> StreamingResponse:
    queue = runtime.bus.subscribe()

    async def event_gen():
        try:
            snapshot = runtime.live_payload("snapshot")
            yield f"data: {json.dumps(snapshot)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                    yield f"data: {json.dumps(event)}\n\n"
                except TimeoutError:
                    now = datetime.now(timezone.utc).isoformat()
                    yield f"data: {json.dumps({'type': 'heartbeat', 'ts': now})}\n\n"
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
