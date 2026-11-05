from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry: float

    def market_value(self, price: float) -> float:
        return self.qty * price

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.avg_entry) * self.qty


@dataclass
class Trade:
    id: str
    symbol: str
    side: str  # buy | sell
    qty: float
    price: float
    fee: float
    ts: datetime
    realized_pnl: float = 0.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "price": self.price,
            "fee": self.fee,
            "ts": self.ts.isoformat(),
            "realized_pnl": self.realized_pnl,
            "note": self.note,
        }


@dataclass
class PaperWallet:
    initial_balance: float
    cash: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    realized_pnl: float = 0.0

    def __post_init__(self) -> None:
        if self.cash == 0.0:
            self.cash = self.initial_balance

    def reset(self, initial_balance: float | None = None) -> None:
        if initial_balance is not None:
            self.initial_balance = initial_balance
        self.cash = self.initial_balance
        self.positions.clear()
        self.trades.clear()
        self.realized_pnl = 0.0

    def restore_from_dict(self, data: dict[str, Any]) -> None:
        self.initial_balance = float(data.get("initial_balance") or self.initial_balance)
        self.cash = float(data.get("cash") if data.get("cash") is not None else self.initial_balance)
        self.realized_pnl = float(data.get("realized_pnl") or 0.0)
        self.positions.clear()
        for row in data.get("positions") or []:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "")
            qty = float(row.get("qty") or 0)
            if not sym or qty <= 0:
                continue
            self.positions[sym] = Position(
                symbol=sym,
                qty=qty,
                avg_entry=float(row.get("avg_entry") or 0),
            )
        self.trades.clear()
        for row in data.get("trades") or []:
            if not isinstance(row, dict):
                continue
            ts_raw = row.get("ts")
            if isinstance(ts_raw, str):
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except ValueError:
                    ts = _utc_now()
            else:
                ts = _utc_now()
            self.trades.append(
                Trade(
                    id=str(row.get("id") or uuid4()),
                    symbol=str(row.get("symbol") or ""),
                    side=str(row.get("side") or "buy"),
                    qty=float(row.get("qty") or 0),
                    price=float(row.get("price") or 0),
                    fee=float(row.get("fee") or 0),
                    ts=ts,
                    realized_pnl=float(row.get("realized_pnl") or 0),
                    note=str(row.get("note") or ""),
                )
            )

    def get_position(self, symbol: str) -> Position | None:
        return self.positions.get(symbol)

    def equity(self, prices: dict[str, float]) -> float:
        total = self.cash
        for sym, pos in self.positions.items():
            px = prices.get(sym, pos.avg_entry)
            total += pos.market_value(px)
        return total

    def unrealized_pnl(self, prices: dict[str, float]) -> float:
        total = 0.0
        for sym, pos in self.positions.items():
            px = prices.get(sym, pos.avg_entry)
            total += pos.unrealized_pnl(px)
        return total

    def buy(self, symbol: str, qty: float, price: float, fee_bps: float, note: str = "") -> Trade:
        if qty <= 0 or price <= 0:
            raise ValueError("qty and price must be positive")
        notional = qty * price
        fee = notional * (fee_bps / 10_000.0)
        cost = notional + fee
        if cost > self.cash:
            raise ValueError("insufficient cash")

        self.cash -= cost
        existing = self.positions.get(symbol)
        if existing:
            new_qty = existing.qty + qty
            existing.avg_entry = (
                (existing.avg_entry * existing.qty) + (price * qty)
            ) / new_qty
            existing.qty = new_qty
        else:
            self.positions[symbol] = Position(symbol=symbol, qty=qty, avg_entry=price)

        trade = Trade(
            id=str(uuid4()),
            symbol=symbol,
            side="buy",
            qty=qty,
            price=price,
            fee=fee,
            ts=_utc_now(),
            realized_pnl=0.0,
            note=note,
        )
        self.trades.append(trade)
        return trade

    def sell(self, symbol: str, qty: float, price: float, fee_bps: float, note: str = "") -> Trade:
        if qty <= 0 or price <= 0:
            raise ValueError("qty and price must be positive")
        existing = self.positions.get(symbol)
        if not existing or existing.qty < qty:
            raise ValueError("insufficient position")

        notional = qty * price
        fee = notional * (fee_bps / 10_000.0)
        proceeds = notional - fee
        realized = (price - existing.avg_entry) * qty - fee

        existing.qty -= qty
        if existing.qty <= 1e-12:
            del self.positions[symbol]

        self.cash += proceeds
        self.realized_pnl += realized

        trade = Trade(
            id=str(uuid4()),
            symbol=symbol,
            side="sell",
            qty=qty,
            price=price,
            fee=fee,
            ts=_utc_now(),
            realized_pnl=realized,
            note=note,
        )
        self.trades.append(trade)
        return trade

    def snapshot(self, prices: dict[str, float]) -> dict[str, Any]:
        positions = []
        for sym, pos in self.positions.items():
            px = prices.get(sym, pos.avg_entry)
            positions.append(
                {
                    "symbol": pos.symbol,
                    "qty": pos.qty,
                    "avg_entry": pos.avg_entry,
                    "mark_price": px,
                    "market_value": pos.market_value(px),
                    "unrealized_pnl": pos.unrealized_pnl(px),
                }
            )
        equity = self.equity(prices)
        unrealized = self.unrealized_pnl(prices)
        return {
            "initial_balance": self.initial_balance,
            "cash": self.cash,
            "equity": equity,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": unrealized,
            "total_pnl": equity - self.initial_balance,
            "positions": positions,
            "trade_count": len(self.trades),
        }
