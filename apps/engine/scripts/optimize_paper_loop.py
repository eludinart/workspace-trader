"""Paper-only recursive agressif optimizer: retrain → 30d backtest → adjust until PnL > 0."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.backtest import _as_utc, _max_drawdown, run_backtest  # noqa: E402
from app.config import BotParams  # noqa: E402
from app.execution import buy_spend, cooldown_active, plan_protective, plan_signal  # noqa: E402
from app.market.coinbase import CoinbaseClient  # noqa: E402
from app.strategy.xgboost_strategy import XGBoostStrategy  # noqa: E402
from app.wallet import PaperWallet  # noqa: E402

DATA = ROOT / "data"
MAX_ITERS = 12
INITIAL = 800.0
TRAIN_EVERY = 4  # speed: retrain every N bars during search
TRAIN_WINDOW = 120


def _params(**overrides) -> BotParams:
    base = dict(
        symbol="BTC-USD",
        trading_style="agressif",
        buy_threshold=0.55,
        sell_threshold=0.50,
        take_profit_pct=0.027,
        stop_loss_pct=0.010,
        min_hold_min=5,
        max_open_symbols=6,
        reward_risk=2.0,
        risk_per_trade_pct=0.018,
        max_position_pct=0.18,
        position_size_pct=0.12,
        initial_balance=INITIAL,
        candle_granularity_sec=3600,
        label_horizon_bars=3,
        label_threshold_pct=0.027,
        label_path_aware=True,
        add_cooldown_min=8,
        aggressive_sell_confidence=0.65,
        fee_bps=10.0,
        slippage_bps=5.0,
    )
    base.update(overrides)
    return BotParams(**base)


def _snapshot(p: BotParams) -> dict:
    keys = [
        "buy_threshold",
        "sell_threshold",
        "take_profit_pct",
        "stop_loss_pct",
        "min_hold_min",
        "max_open_symbols",
        "position_size_pct",
        "label_horizon_bars",
        "label_threshold_pct",
        "reward_risk",
        "add_cooldown_min",
    ]
    return {k: getattr(p, k) for k in keys}


def run_fast_backtest(candles, params: BotParams) -> dict:
    """Walk-forward paper backtest; retrain every TRAIN_EVERY bars for search speed."""
    wallet = PaperWallet(initial_balance=params.initial_balance)
    strategy = XGBoostStrategy()
    equity_curve: list[float] = []
    wins = losses = closed = 0
    last_buy_at = None
    last_entry_bar: int | None = None
    accs: list[float] = []
    trades = 0

    for i in range(TRAIN_WINDOW, len(candles)):
        window = candles.iloc[: i + 1].copy()
        if (i - TRAIN_WINDOW) % TRAIN_EVERY == 0 or strategy.model is None:
            tr = strategy.train(window.iloc[:-1], params)
            if tr.get("accuracy") is not None:
                accs.append(float(tr["accuracy"]))
        signal = strategy.predict(window, params)
        bar = window.iloc[-1]
        close = float(bar["close"])
        high = float(bar["high"])
        low = float(bar["low"])
        bar_ts = _as_utc(bar["time"])
        slip = params.slippage_bps / 10_000.0
        trade = None
        note = ""
        pos = wallet.get_position(params.symbol)

        if pos is not None:
            stop_plan = plan_protective(low, pos.avg_entry, params.take_profit_pct, params.stop_loss_pct)
            take_plan = plan_protective(high, pos.avg_entry, params.take_profit_pct, params.stop_loss_pct)
            protective = None
            raw_fill = close
            if stop_plan is not None and stop_plan.reason == "sl":
                protective = stop_plan
                raw_fill = min(low, pos.avg_entry * (1.0 - params.stop_loss_pct))
            elif take_plan is not None and take_plan.reason == "tp":
                protective = take_plan
                raw_fill = pos.avg_entry * (1.0 + params.take_profit_pct)
            if protective is not None and raw_fill > 0:
                try:
                    trade = wallet.sell(
                        params.symbol,
                        qty=pos.qty,
                        price=raw_fill * (1.0 - slip),
                        fee_bps=params.fee_bps,
                        note=protective.note,
                    )
                    note = protective.note
                except ValueError:
                    trade = None

        if trade is None:
            pos = wallet.get_position(params.symbol)
            # Enforce min_hold on signal exits during optimization.
            held_ok = True
            if pos is not None and last_entry_bar is not None and params.min_hold_min > 0:
                # 1h bars: min_hold_min minutes → bars
                need = max(1, int(params.min_hold_min / (params.candle_granularity_sec / 60)))
                held_ok = (i - last_entry_bar) >= need
            equity = wallet.equity({params.symbol: close})
            pos_val = pos.market_value(close) if pos else 0.0
            action = signal.action
            if action == "sell" and not held_ok:
                action = "hold"
            plan = plan_signal(
                action,
                signal.confidence,
                has_position=pos is not None,
                position_value=pos_val,
                equity=equity,
                max_position_pct=params.max_position_pct,
                position_size_pct=params.position_size_pct,
                open_symbols=1 if pos else 0,
                max_open_symbols=params.max_open_symbols,
                cooldown_active=(
                    cooldown_active(last_buy_at, bar_ts, params.add_cooldown_min) if pos else False
                ),
                aggressive_sell_confidence=params.aggressive_sell_confidence,
                sell_threshold=params.sell_threshold,
            )
            note = plan.note
            if plan.side == "buy":
                fill = close * (1.0 + slip)
                spend = buy_spend(
                    wallet.cash, equity, pos_val, params.position_size_pct, params.max_position_pct
                )
                fee_mult = 1.0 + params.fee_bps / 10_000.0
                if spend >= 10 and fill > 0:
                    qty = spend / (fill * fee_mult)
                    try:
                        trade = wallet.buy(
                            params.symbol, qty=qty, price=fill, fee_bps=params.fee_bps, note=plan.note
                        )
                        last_buy_at = bar_ts
                        last_entry_bar = i
                    except ValueError:
                        trade = None
            elif plan.side == "sell" and pos is not None:
                fill = close * (1.0 - slip)
                try:
                    trade = wallet.sell(
                        params.symbol, qty=pos.qty, price=fill, fee_bps=params.fee_bps, note=plan.note
                    )
                except ValueError:
                    trade = None

        if trade is not None:
            trades += 1
            if trade.side == "sell":
                closed += 1
                if trade.realized_pnl >= 0:
                    wins += 1
                else:
                    losses += 1
                last_entry_bar = None

        equity_curve.append(wallet.equity({params.symbol: close}))

    final_px = float(candles.iloc[-1]["close"])
    snap = wallet.snapshot({params.symbol: final_px})
    decided = wins + losses
    return {
        "ok": True,
        "cash": snap["cash"],
        "final_equity": snap["equity"],
        "total_pnl": snap["total_pnl"],
        "trade_count": trades,
        "closed_roundtrips": closed,
        "win_rate": (wins / decided) if decided else 0.0,
        "wins": wins,
        "losses": losses,
        "max_drawdown": _max_drawdown(equity_curve),
        "mean_accuracy": float(sum(accs) / len(accs)) if accs else None,
        "params": _snapshot(params),
    }


def propose_next(prev: BotParams, result: dict, iteration: int) -> tuple[BotParams, str]:
    """Heuristic adjustments aimed at positive paper PnL."""
    p = prev.model_dump()
    pnl = float(result["total_pnl"])
    wr = float(result["win_rate"])
    trades = int(result["trade_count"])
    dd = float(result["max_drawdown"])
    notes: list[str] = []

    # No trades: labels/thresholds too strict for this window — open the funnel.
    if trades == 0:
        p["label_horizon_bars"] = min(24, max(6, int(p["label_horizon_bars"]) + 3))
        p["label_threshold_pct"] = max(0.010, float(p["label_threshold_pct"]) - 0.005)
        p["buy_threshold"] = max(0.40, float(p["buy_threshold"]) - 0.05)
        p["sell_threshold"] = max(0.40, float(p["sell_threshold"]) - 0.03)
        p["take_profit_pct"] = max(0.014, min(float(p["take_profit_pct"]), float(p["label_threshold_pct"])))
        notes.append(
            f"0 trade→ horizon {p['label_horizon_bars']}, "
            f"label {p['label_threshold_pct']:.3f}, buy {p['buy_threshold']:.2f}"
        )
        return BotParams(**p), "; ".join(notes)

    # Losing or flat with activity: tighten quality / reshape TP-SL.
    if pnl <= 0:
        if wr < 0.45 or trades > 60:
            p["buy_threshold"] = min(0.72, float(p["buy_threshold"]) + 0.03)
            p["sell_threshold"] = min(0.68, float(p["sell_threshold"]) + 0.02)
            notes.append("seuils buy/sell +")
        if trades > 70:
            p["min_hold_min"] = min(30, int(p["min_hold_min"]) + 5)
            p["add_cooldown_min"] = min(40, int(p["add_cooldown_min"]) + 4)
            notes.append("hold/cooldown +")
        if dd > 0.025 or pnl < -10:
            p["stop_loss_pct"] = min(0.018, float(p["stop_loss_pct"]) + 0.002)
            p["position_size_pct"] = max(0.06, float(p["position_size_pct"]) - 0.02)
            p["max_open_symbols"] = max(3, int(p["max_open_symbols"]) - 1)
            notes.append("SL+/size-/slots-")
        if iteration in (1, 2, 5) or trades < 20:
            p["label_horizon_bars"] = min(24, int(p["label_horizon_bars"]) + 3)
            notes.append(f"label_horizon→{p['label_horizon_bars']}")
        if iteration in (3, 6, 9):
            p["label_threshold_pct"] = max(0.012, float(p["label_threshold_pct"]) - 0.003)
            notes.append(f"label_th→{p['label_threshold_pct']:.3f}")
        if iteration in (4, 7):
            p["take_profit_pct"] = max(0.014, float(p["take_profit_pct"]) - 0.003)
            p["reward_risk"] = max(1.4, float(p["reward_risk"]) - 0.1)
            notes.append(f"TP→{p['take_profit_pct']:.3f}")
        if iteration >= 8:
            p["buy_threshold"] = min(0.75, float(p["buy_threshold"]) + 0.02)
            p["take_profit_pct"] = max(0.012, float(p["take_profit_pct"]) - 0.002)
            p["stop_loss_pct"] = min(0.02, float(p["stop_loss_pct"]) + 0.001)
            notes.append("filtre fort + TP↓ SL↑")

    if not notes:
        notes.append("micro-ajustement buy_th +")
        p["buy_threshold"] = min(0.70, float(p["buy_threshold"]) + 0.02)

    return BotParams(**p), "; ".join(notes)


def main() -> int:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    print("fetch candles BTC-USD 30d...", flush=True)
    market = CoinbaseClient()
    candles = market.get_candles_range(
        "BTC-USD", start=start, end=end, granularity=3600, max_candles=5000
    )
    print(f"candles={len(candles)}", flush=True)

    # Reuse cache inside full confirm via monkeypatch.
    def _cached_range(*_a, **_k):
        return candles.copy()

    market.get_candles_range = _cached_range  # type: ignore[method-assign]

    current = _params()
    history: list[dict] = []
    best: dict | None = None

    for it in range(1, MAX_ITERS + 1):
        print(f"\n=== iter {it}/{MAX_ITERS} params={_snapshot(current)} ===", flush=True)
        fast = run_fast_backtest(candles, current)
        if it == 1:
            adj_note = "baseline: labels path 2.7% @ 3h, buy_th 0.55, TP 2.7%, hold 5, slots 6"
        else:
            adj_note = str(history[-1].get("next_adjustments") or "ajustement")
        row = {
            "iteration": it,
            "mode": "fast",
            "adjustments": adj_note,
            "result": {k: fast[k] for k in fast if k != "params"},
            "params": fast["params"],
        }
        history.append(row)
        print(
            f"fast PnL={fast['total_pnl']:.2f} equity={fast['final_equity']:.2f} "
            f"trades={fast['trade_count']} wr={fast['win_rate']:.1%} "
            f"dd={fast['max_drawdown']:.2%} acc={fast['mean_accuracy']}",
            flush=True,
        )
        if best is None or float(fast["total_pnl"]) > float(best["result"]["total_pnl"]):
            best = row

        if float(fast["total_pnl"]) > 0 and int(fast["trade_count"]) >= 5:
            print("positive on fast path — confirming with full walk-forward...", flush=True)
            full = run_backtest(
                market,
                symbol="BTC-USD",
                start=start,
                end=end,
                params=current,
            )
            # accuracy from a quick re-eval
            confirm = {
                "iteration": it,
                "mode": "full_confirm",
                "adjustments": row["adjustments"],
                "params": _snapshot(current),
                "result": {
                    "cash": full.get("cash", full.get("final_equity")),
                    "final_equity": full["final_equity"],
                    "total_pnl": full["total_pnl"],
                    "trade_count": full["trade_count"],
                    "win_rate": full["win_rate"],
                    "max_drawdown": full["max_drawdown"],
                    "closed_roundtrips": full.get("closed_roundtrips"),
                    "wins": full.get("wins"),
                    "losses": full.get("losses"),
                    "mean_accuracy": fast.get("mean_accuracy"),
                },
            }
            history.append(confirm)
            if float(full["total_pnl"]) > 0:
                payload = {
                    "ok": True,
                    "positive": True,
                    "iterations": it,
                    "history": history,
                    "final": confirm,
                    "baseline_prev_agressif": {
                        "cash": 782.5953524336389,
                        "final_equity": 782.5953524336389,
                        "total_pnl": -17.404647566361064,
                        "trade_count": 78,
                        "win_rate": 0.41379310344827586,
                        "max_drawdown": 0.026486731030679722,
                    },
                }
                DATA.mkdir(parents=True, exist_ok=True)
                (DATA / "optimize_loop_result.json").write_text(
                    json.dumps(payload, indent=2, default=str), encoding="utf-8"
                )
                summary = {**full, **confirm["params"], "mean_accuracy": fast.get("mean_accuracy")}
                keep = {
                    k: summary[k]
                    for k in [
                        "ok",
                        "symbol",
                        "cash",
                        "final_equity",
                        "total_pnl",
                        "trade_count",
                        "win_rate",
                        "max_drawdown",
                        "buy_threshold",
                        "take_profit_pct",
                        "stop_loss_pct",
                        "min_hold_min",
                        "max_open_symbols",
                        "label_horizon_bars",
                        "label_threshold_pct",
                        "mean_accuracy",
                    ]
                    if k in summary
                }
                keep["trading_style"] = "agressif"
                (DATA / "last_backtest.json").write_text(
                    json.dumps(keep, indent=2, default=str), encoding="utf-8"
                )
                print(json.dumps(payload["final"], indent=2, default=str), flush=True)
                return 0
            print("full confirm still negative — continue", flush=True)
            fast = {
                **fast,
                "total_pnl": full["total_pnl"],
                "final_equity": full["final_equity"],
                "win_rate": full["win_rate"],
                "trade_count": full["trade_count"],
                "max_drawdown": full["max_drawdown"],
            }

        current, adj = propose_next(current, fast, it)
        history[-1]["next_adjustments"] = adj
        print(f"next: {adj}", flush=True)

    payload = {
        "ok": True,
        "positive": False,
        "iterations": MAX_ITERS,
        "history": history,
        "best": best,
        "message": "max iterations reached without positive PnL",
    }
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "optimize_loop_result.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps({"positive": False, "best": best}, indent=2, default=str), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
