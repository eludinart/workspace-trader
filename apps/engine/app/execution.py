"""Order planning for the paper engine: exits, size caps, and signal mapping.

Pure functions so the live loop and the backtest share one policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# A position this close to its equity cap is "heavily long": a sell signal
# closes it instead of nibbling.
_HEAVY_LONG_RATIO = 0.8
_MAX_SIZE_EPS = 0.995


@dataclass(frozen=True)
class OrderPlan:
    side: str  # buy | sell | hold
    sell_fraction: float
    reason: str  # signal | tp | sl | kill | manual | hold | max_size | flat | cooldown | max_symbols | budget | fee_guard | min_hold
    note: str


def plan_protective(
    price: float,
    avg_entry: float,
    take_profit_pct: float,
    stop_loss_pct: float,
) -> OrderPlan | None:
    """Full exit when mark breaches take-profit or stop-loss. Stop wins if both match."""
    if price <= 0 or avg_entry <= 0:
        return None
    change = (price - avg_entry) / avg_entry
    if stop_loss_pct > 0 and change <= -stop_loss_pct:
        return OrderPlan("sell", 1.0, "sl", "exit:sl")
    if take_profit_pct > 0 and change >= take_profit_pct:
        return OrderPlan("sell", 1.0, "tp", "exit:tp")
    return None


def sell_fraction(
    confidence: float,
    *,
    sell_threshold: float,
    aggressive_confidence: float,
    position_size_pct: float,
    heavily_long: bool,
) -> float:
    """Always full exit on discretionary sells — partial clips churn fees on small books."""
    del confidence, sell_threshold, aggressive_confidence, position_size_pct, heavily_long
    return 1.0


def plan_signal(
    action: str,
    confidence: float,
    *,
    has_position: bool,
    position_value: float,
    equity: float,
    max_position_pct: float,
    position_size_pct: float,
    open_symbols: int,
    max_open_symbols: int,
    cooldown_active: bool,
    aggressive_sell_confidence: float,
    sell_threshold: float,
) -> OrderPlan:
    """Map a model action onto an order the book can actually hold."""
    cap = max_position_pct * equity if equity > 0 else 0.0
    at_max = has_position and cap > 0 and position_value >= cap * _MAX_SIZE_EPS
    heavily_long = has_position and cap > 0 and position_value >= cap * _HEAVY_LONG_RATIO

    if action == "buy":
        if not has_position and open_symbols >= max_open_symbols:
            return OrderPlan("hold", 0.0, "max_symbols", "hold:max_symbols")
        if has_position and cooldown_active:
            return OrderPlan("hold", 0.0, "cooldown", "hold:cooldown")
        if at_max:
            return OrderPlan("hold", 0.0, "max_size", "hold:max_size")
        return OrderPlan("buy", 0.0, "signal", "entry:signal")

    if action == "sell":
        if not has_position:
            return OrderPlan("hold", 0.0, "flat", "hold:flat")
        fraction = sell_fraction(
            confidence,
            sell_threshold=sell_threshold,
            aggressive_confidence=aggressive_sell_confidence,
            position_size_pct=position_size_pct,
            heavily_long=heavily_long,
        )
        return OrderPlan("sell", fraction, "signal", "exit:signal")

    return OrderPlan("hold", 0.0, "hold", "")


def buy_spend(
    cash: float,
    equity: float,
    position_value: float,
    position_size_pct: float,
    max_position_pct: float,
) -> float:
    """Cash to deploy on a buy, clipped by the per-symbol equity cap."""
    if cash <= 0 or equity <= 0 or position_size_pct <= 0 or max_position_pct <= 0:
        return 0.0
    room = max(0.0, max_position_pct * equity - position_value)
    return max(0.0, min(cash * position_size_pct, room, cash))


def cooldown_active(last_buy: datetime | None, now: datetime, cooldown_min: int) -> bool:
    if cooldown_min <= 0 or last_buy is None:
        return False
    elapsed = (now - last_buy).total_seconds()
    return elapsed < cooldown_min * 60
