from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.config import BotParams
from app.execution import buy_spend, cooldown_active, plan_protective, plan_signal
from app.fill_model import impact_bps_for_notional, quote_fill
from app.journal import DecisionJournal
from app.market.coinbase import CoinbaseClient
from app.strategy.xgboost_strategy import XGBoostStrategy
from app.wallet import PaperWallet


def _as_utc(ts: Any) -> datetime:
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    if not isinstance(ts, datetime):
        return datetime.now(timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            dd = (peak - eq) / peak
            max_dd = max(max_dd, dd)
    return float(max_dd)


def run_backtest(
    market: CoinbaseClient,
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    params: BotParams | None = None,
    train_window: int = 120,
) -> dict[str, Any]:
    """
    Replay Coinbase history with the XGBoost strategy (walk-forward).
    Returns performance report + equity curve + decision samples.
    """
    params = params or BotParams(symbol=symbol)
    params = params.model_copy(update={"symbol": symbol})

    candles = market.get_candles_range(
        symbol,
        start=start,
        end=end,
        granularity=params.candle_granularity_sec,
        max_candles=5000,
    )
    if len(candles) < train_window + 10:
        return {
            "ok": False,
            "error": "insufficient_candles",
            "candle_count": len(candles),
            "symbol": symbol,
        }

    wallet = PaperWallet(initial_balance=params.initial_balance)
    strategy = XGBoostStrategy()
    journal = DecisionJournal(max_entries=2000)

    equity_curve: list[dict[str, Any]] = []
    wins = 0
    losses = 0
    closed_trades = 0
    last_buy_at: datetime | None = None

    for i in range(train_window, len(candles)):
        window = candles.iloc[: i + 1].copy()
        # Train on all but last bar of window for stability
        train_df = window.iloc[:-1]
        strategy.train(train_df, params)
        signal = strategy.predict(window, params)
        bar = window.iloc[-1]
        close = float(bar["close"])
        high = float(bar["high"])
        low = float(bar["low"])
        ts = bar["time"]
        bar_ts = _as_utc(ts)
        top = strategy.top_features(signal.features, n=5)
        explanation = strategy.explain(signal)

        trade = None
        note = ""
        latency_ms = int(getattr(params, "latency_ms", 0) or 0)
        impact_on = bool(getattr(params, "order_impact_enabled", False))
        impact_max = float(getattr(params, "impact_bps_max", 5.0) or 0.0)

        def _fill(side: str, decision_mid: float, notional: float):
            impact = impact_bps_for_notional(
                notional,
                enabled=impact_on,
                equity=max(wallet.equity({symbol: close}), 1.0),
                max_impact_bps=impact_max,
            )
            return quote_fill(
                side,
                decision_mid,
                slip_bps=float(params.slippage_bps),
                latency_ms=latency_ms,
                impact_bps=impact,
                fill_mid=decision_mid,
                decision_ts=bar_ts,
            )

        pos = wallet.get_position(symbol)
        if pos is not None:
            stop_plan = plan_protective(
                low, pos.avg_entry, params.take_profit_pct, params.stop_loss_pct
            )
            take_plan = plan_protective(
                high, pos.avg_entry, params.take_profit_pct, params.stop_loss_pct
            )
            protective = None
            raw_fill = close
            # Same bar: honor the stop. A gap through the stop fills at the worse price.
            if stop_plan is not None and stop_plan.reason == "sl":
                protective = stop_plan
                raw_fill = min(low, pos.avg_entry * (1.0 - params.stop_loss_pct))
            elif take_plan is not None and take_plan.reason == "tp":
                protective = take_plan
                raw_fill = pos.avg_entry * (1.0 + params.take_profit_pct)
            if protective is not None and raw_fill > 0:
                q = _fill("sell", raw_fill, pos.qty * raw_fill)
                try:
                    trade = wallet.sell(
                        symbol,
                        qty=pos.qty,
                        price=q.fill_price,
                        fee_bps=params.fee_bps,
                        note=protective.note,
                        decision_ts=q.decision_ts,
                        decision_price=q.decision_price,
                        latency_ms=q.latency_ms,
                        slip_bps=q.slip_bps,
                        impact_bps=q.impact_bps,
                        ts=bar_ts,
                    )
                    note = protective.note
                except ValueError:
                    trade = None

        if trade is None:
            pos = wallet.get_position(symbol)
            equity = wallet.equity({symbol: close})
            pos_val = pos.market_value(close) if pos else 0.0
            plan = plan_signal(
                signal.action,
                signal.confidence,
                has_position=pos is not None,
                position_value=pos_val,
                equity=equity,
                max_position_pct=params.max_position_pct,
                position_size_pct=params.position_size_pct,
                open_symbols=1 if pos else 0,
                max_open_symbols=params.max_open_symbols,
                cooldown_active=(
                    cooldown_active(last_buy_at, bar_ts, params.add_cooldown_min)
                    if pos is not None
                    else False
                ),
                aggressive_sell_confidence=params.aggressive_sell_confidence,
                sell_threshold=params.sell_threshold,
            )
            note = plan.note
            if plan.side == "buy":
                spend = buy_spend(
                    wallet.cash,
                    equity,
                    pos_val,
                    params.position_size_pct,
                    params.max_position_pct,
                )
                q = _fill("buy", close, spend)
                fee_mult = 1.0 + params.fee_bps / 10_000.0
                if spend >= 10 and q.fill_price > 0:
                    qty = spend / (q.fill_price * fee_mult)
                    try:
                        trade = wallet.buy(
                            symbol,
                            qty=qty,
                            price=q.fill_price,
                            fee_bps=params.fee_bps,
                            note=plan.note,
                            decision_ts=q.decision_ts,
                            decision_price=q.decision_price,
                            latency_ms=q.latency_ms,
                            slip_bps=q.slip_bps,
                            impact_bps=q.impact_bps,
                            ts=bar_ts,
                        )
                        last_buy_at = bar_ts
                    except ValueError:
                        trade = None
            elif plan.side == "sell" and pos is not None:
                qty = pos.qty * plan.sell_fraction
                if pos.qty - qty < 1e-8:
                    qty = pos.qty
                q = _fill("sell", close, qty * close)
                try:
                    trade = wallet.sell(
                        symbol,
                        qty=qty,
                        price=q.fill_price,
                        fee_bps=params.fee_bps,
                        note=plan.note,
                        decision_ts=q.decision_ts,
                        decision_price=q.decision_price,
                        latency_ms=q.latency_ms,
                        slip_bps=q.slip_bps,
                        impact_bps=q.impact_bps,
                        ts=bar_ts,
                    )
                except ValueError:
                    trade = None

        if trade is not None and trade.side == "sell":
            closed_trades += 1
            if trade.realized_pnl >= 0:
                wins += 1
            else:
                losses += 1

        if trade is not None:
            if note and note not in explanation:
                explanation = f"{explanation} | {note}"
            journal.add(
                symbol=symbol,
                action=trade.side,
                price=trade.price,
                qty=trade.qty,
                trade_id=trade.id,
                confidence=signal.confidence,
                features=signal.features,
                top_features=top,
                explanation=explanation,
                simulated=True,
                source="backtest",
                ts=bar_ts,
                note=note,
            )

        equity = wallet.equity({symbol: close})
        equity_curve.append({"time": str(ts), "equity": equity})

    final_price = float(candles.iloc[-1]["close"])
    snap = wallet.snapshot({symbol: final_price})
    decided = wins + losses
    win_rate = (wins / decided) if decided else 0.0
    eq_values = [p["equity"] for p in equity_curve]

    return {
        "ok": True,
        "symbol": symbol,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "candle_count": len(candles),
        "initial_balance": params.initial_balance,
        "cash": snap["cash"],
        "final_equity": snap["equity"],
        "total_pnl": snap["total_pnl"],
        "realized_pnl": snap["realized_pnl"],
        "unrealized_pnl": snap["unrealized_pnl"],
        "positions": snap.get("positions", []),
        "trade_count": len(wallet.trades),
        "closed_roundtrips": closed_trades,
        "win_rate": win_rate,
        "wins": wins,
        "losses": losses,
        "max_drawdown": _max_drawdown(eq_values),
        "equity_curve": equity_curve[:: max(1, len(equity_curve) // 200)],
        "decisions": journal.list(limit=50, source="backtest"),
        "trades": [t.to_dict() for t in wallet.trades[-50:]],
    }
