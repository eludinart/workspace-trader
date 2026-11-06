"""Professional risk helpers: costs, ATR stops, and position sizing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


def round_trip_cost_frac(fee_bps: float, slippage_bps: float) -> float:
    """Approximate adverse cost of a round-trip as a fraction of price."""
    return 2.0 * (max(0.0, fee_bps) + max(0.0, slippage_bps)) / 10_000.0


def atr_from_candles(candles: pd.DataFrame, period: int = 14) -> float | None:
    """Wilder-style ATR from OHLC frame. Returns None if not enough rows."""
    if candles is None or len(candles) < period + 1:
        return None
    need = {"high", "low", "close"}
    if not need.issubset(set(candles.columns)):
        return None
    high = candles["high"].astype(float)
    low = candles["low"].astype(float)
    close = candles["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.tail(period).mean()
    if atr is None or float(atr) <= 0:
        return None
    return float(atr)


def atr_pct(price: float, atr: float | None) -> float | None:
    if price <= 0 or atr is None or atr <= 0:
        return None
    return atr / price


@dataclass(frozen=True)
class ExitLevels:
    stop_pct: float
    take_pct: float
    atr: float | None
    atr_pct: float | None


def compute_exit_levels(
    price: float,
    candles: pd.DataFrame | None,
    *,
    fee_bps: float,
    slippage_bps: float,
    atr_period: int = 14,
    stop_atr_mult: float = 1.5,
    reward_risk: float = 2.0,
    floor_stop_pct: float = 0.007,
    cap_stop_pct: float = 0.03,
) -> ExitLevels:
    """ATR-based stop with TP at reward_risk × stop, both above round-trip costs."""
    cost = round_trip_cost_frac(fee_bps, slippage_bps)
    atr = atr_from_candles(candles, atr_period) if candles is not None else None
    ap = atr_pct(price, atr)
    if ap is not None:
        stop = max(floor_stop_pct, min(cap_stop_pct, stop_atr_mult * ap))
    else:
        stop = max(floor_stop_pct, cost * 2.5)
    # TP clears fees with edge; cap runaway targets so winners actually bank.
    take = max(stop * reward_risk, cost * 2.2, stop + cost)
    take = min(take, max(0.035, stop * reward_risk))
    return ExitLevels(stop_pct=stop, take_pct=take, atr=atr, atr_pct=ap)


def risk_position_spend(
    equity: float,
    cash: float,
    entry: float,
    stop_pct: float,
    *,
    risk_frac: float = 0.01,
    max_position_pct: float = 0.3,
    min_notional: float = 15.0,
) -> float:
    """Cash to deploy so a stop hit ≈ risk_frac of equity (1% rule)."""
    if equity <= 0 or cash <= 0 or entry <= 0 or stop_pct <= 0:
        return 0.0
    risk_dollars = equity * risk_frac
    # Loss ≈ notional * stop_pct (plus fees approximated in stop already).
    notional = risk_dollars / stop_pct
    notional = min(notional, equity * max_position_pct, cash)
    if notional < min_notional:
        return 0.0
    return float(notional)


def sell_is_net_positive(
    avg_entry: float,
    mark: float,
    *,
    fee_bps: float,
    slippage_bps: float,
    min_edge_frac: float = 0.0005,
) -> bool:
    """True if a full exit at mark would be flat-to-green after costs."""
    if avg_entry <= 0 or mark <= 0:
        return False
    cost = round_trip_cost_frac(fee_bps, slippage_bps)
    change = (mark - avg_entry) / avg_entry
    return change >= cost + min_edge_frac


def net_pnl_if_closed(
    avg_entry: float,
    qty: float,
    mark: float,
    *,
    fee_bps: float,
    slippage_bps: float,
    cost_basis: float | None = None,
) -> dict[str, float | bool]:
    """Cash result of closing now — same basis as ``PaperWallet.sell``.

    Entry fill slippage is already inside ``avg_entry``. Prefer open ``cost_basis``
    (notionals + entry fees). Else reconstruct entry fee from ``fee_bps``.
    Exit slippage is applied to mark for a hypothetical fill (not double-counted
    against a fill that already embeds slip).
    """
    if avg_entry <= 0 or qty <= 0 or mark <= 0:
        return {
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "net_pnl_pct": 0.0,
            "fees_total": 0.0,
            "cost_basis": 0.0,
            "would_be_green": False,
        }
    fee = max(0.0, float(fee_bps)) / 10_000.0
    slip = max(0.0, float(slippage_bps)) / 10_000.0
    entry_notional = avg_entry * qty
    if cost_basis is not None and float(cost_basis) > 0:
        basis = float(cost_basis)
        entry_fee = max(0.0, basis - entry_notional)
    else:
        entry_fee = entry_notional * fee
        basis = entry_notional + entry_fee
    sell_fill = mark * (1.0 - slip)
    exit_notional = sell_fill * qty
    exit_fee = exit_notional * fee
    proceeds = exit_notional - exit_fee
    gross = (mark - avg_entry) * qty
    net = proceeds - basis
    net_pct = (net / basis) * 100.0 if basis > 0 else 0.0
    return {
        "gross_pnl": float(gross),
        "net_pnl": float(net),
        "net_pnl_pct": float(net_pct),
        "fees_total": float(entry_fee + exit_fee),
        "cost_basis": float(basis),
        "would_be_green": bool(net > 0),
    }


def annotate_levels(levels: ExitLevels) -> dict[str, Any]:
    return {
        "stop_pct": levels.stop_pct,
        "take_pct": levels.take_pct,
        "atr": levels.atr,
        "atr_pct": levels.atr_pct,
    }


def dynamic_max_open(
    equity: float,
    *,
    capital_per_slot: float = 80.0,
    hard_cap: int = 30,
    floor: int = 5,
) -> int:
    """Scale concurrent position slots with equity (~10 slots at 800)."""
    if equity <= 0 or capital_per_slot <= 0:
        return floor
    return int(min(hard_cap, max(floor, equity // capital_per_slot)))


def portfolio_open_risk_r(
    positions: dict[str, Any],
    prices: dict[str, float],
    exit_levels: dict[str, dict[str, Any]],
    equity: float,
    default_stop_pct: float,
) -> float:
    """Sum of open risk in R units (1R = 1% equity) across positions."""
    if equity <= 0:
        return 0.0
    one_r = equity * 0.01
    if one_r <= 0:
        return 0.0
    total = 0.0
    for sym, pos in positions.items():
        mark = float(prices.get(sym) or getattr(pos, "avg_entry", 0) or 0)
        qty = float(getattr(pos, "qty", 0) or 0)
        if mark <= 0 or qty <= 0:
            continue
        stop_pct = float((exit_levels.get(sym) or {}).get("stop_pct") or default_stop_pct or 0.012)
        risk_dollars = mark * qty * stop_pct
        total += risk_dollars / one_r
    return float(total)
