from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry: float
    # Total quote spent (notionals + entry fees) for open qty — single cost basis.
    cost_basis: float = 0.0

    def market_value(self, price: float) -> float:
        return self.qty * price

    def unrealized_pnl(self, price: float) -> float:
        """Mark vs fill avg (fees not in avg_entry). Prefer net via risk.net_pnl_if_closed."""
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
    # Fill meta (decision → fill). Optional on legacy rows.
    decision_ts: datetime | None = None
    decision_price: float | None = None
    latency_ms: int | None = None
    slip_bps: float | None = None
    impact_bps: float | None = None
    # Accounting extras
    invested_quote: float | None = None  # buys: notional + fee
    avg_entry_ref: float | None = None  # sells: avg entry at exit
    cost_basis_closed: float | None = None  # sells: proportional cost basis
    reference_ts: datetime | None = None
    meta_complete: bool = True

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
            "decision_ts": self.decision_ts.isoformat() if self.decision_ts else None,
            "decision_price": self.decision_price,
            "latency_ms": self.latency_ms,
            "slip_bps": self.slip_bps,
            "impact_bps": self.impact_bps,
            "invested_quote": self.invested_quote,
            "avg_entry_ref": self.avg_entry_ref,
            "cost_basis_closed": self.cost_basis_closed,
            "reference_ts": self.reference_ts.isoformat() if self.reference_ts else None,
            "meta_complete": self.meta_complete,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> Trade:
        ts = _parse_ts(row.get("ts")) or _utc_now()
        meta_raw = row.get("meta_complete")
        if meta_raw is None:
            # Legacy rows without fill meta are incomplete for display enrichment.
            has_meta = row.get("avg_entry_ref") is not None or row.get("invested_quote") is not None
            meta_complete = bool(has_meta)
        else:
            meta_complete = bool(meta_raw)
        return cls(
            id=str(row.get("id") or uuid4()),
            symbol=str(row.get("symbol") or ""),
            side=str(row.get("side") or "buy"),
            qty=float(row.get("qty") or 0),
            price=float(row.get("price") or 0),
            fee=float(row.get("fee") or 0),
            ts=ts,
            realized_pnl=float(row.get("realized_pnl") or 0),
            note=str(row.get("note") or ""),
            decision_ts=_parse_ts(row.get("decision_ts")),
            decision_price=(
                float(row["decision_price"]) if row.get("decision_price") is not None else None
            ),
            latency_ms=int(row["latency_ms"]) if row.get("latency_ms") is not None else None,
            slip_bps=float(row["slip_bps"]) if row.get("slip_bps") is not None else None,
            impact_bps=float(row["impact_bps"]) if row.get("impact_bps") is not None else None,
            invested_quote=(
                float(row["invested_quote"]) if row.get("invested_quote") is not None else None
            ),
            avg_entry_ref=(
                float(row["avg_entry_ref"]) if row.get("avg_entry_ref") is not None else None
            ),
            cost_basis_closed=(
                float(row["cost_basis_closed"])
                if row.get("cost_basis_closed") is not None
                else None
            ),
            reference_ts=_parse_ts(row.get("reference_ts")),
            meta_complete=meta_complete,
        )


def annotate_trade_for_display(trade: Trade) -> dict[str, Any]:
    """UI fields for Historic paper / LiveOrders — single accounting story.

    Sell: realized net quote (fees in, slip already in fill). Buy: invested quote.
    Incomplete legacy → grey / non calculable (no invented retroactive PnL).
    """
    base = trade.to_dict()
    side = trade.side.lower()
    if side == "buy":
        invested = trade.invested_quote
        if invested is None and trade.qty > 0 and trade.price > 0:
            if trade.meta_complete:
                invested = trade.qty * trade.price + trade.fee
            else:
                invested = None
        if invested is None:
            return {
                **base,
                "display_kind": "incomplete",
                "display_amount": None,
                "display_pct": None,
                "badge": None,
                "badge_label": "Non calculable",
                "amount_label": "Non calculable — données incomplètes",
                "reference_label": None,
                "tone": "muted",
            }
        return {
            **base,
            "display_kind": "invested",
            "display_amount": float(invested),
            "display_pct": None,
            "badge": None,
            "badge_label": "Investi",
            "amount_label": "Montant investi",
            "reference_label": None,
            "tone": "neutral",
        }

    if side == "sell":
        net = float(trade.realized_pnl)
        if net > 0:
            badge, tone, label = "winner", "gain", "Gagnant"
        elif net < 0:
            badge, tone, label = "loser", "loss", "Perdant"
        else:
            badge, tone, label = "flat", "neutral", "Équilibre"

        complete = (
            trade.meta_complete
            and trade.avg_entry_ref is not None
            and trade.cost_basis_closed is not None
        )
        if not complete:
            # Stored fill PnL is not invented — show it without % / entry ref.
            return {
                **base,
                "display_kind": "realized",
                "display_amount": net,
                "display_pct": None,
                "badge": badge,
                "badge_label": label,
                "amount_label": "P&L enregistré — réf. incomplète",
                "reference_label": None,
                "tone": tone,
            }

        basis = float(trade.cost_basis_closed)
        pct = (net / basis) * 100.0 if basis > 0 else None
        ref_ts = trade.reference_ts.isoformat() if trade.reference_ts else None
        return {
            **base,
            "display_kind": "realized",
            "display_amount": net,
            "display_pct": pct,
            "badge": badge,
            "badge_label": label,
            "amount_label": "P&L réalisé net",
            "reference_label": "Prix moyen d'entrée",
            "reference_price": float(trade.avg_entry_ref),
            "reference_nature": "avg_entry",
            "reference_ts": ref_ts,
            "tone": tone,
        }

    return {
        **base,
        "display_kind": "incomplete",
        "display_amount": None,
        "display_pct": None,
        "badge": None,
        "badge_label": "Non calculable",
        "amount_label": "Non calculable — données incomplètes",
        "reference_label": None,
        "tone": "muted",
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
            avg_entry = float(row.get("avg_entry") or 0)
            cost_basis = row.get("cost_basis")
            if cost_basis is None:
                # Legacy: approximate cost as notional only (no entry fee) — incomplete.
                cost_basis = avg_entry * qty
            self.positions[sym] = Position(
                symbol=sym,
                qty=qty,
                avg_entry=avg_entry,
                cost_basis=float(cost_basis),
            )
        self.trades.clear()
        for row in data.get("trades") or []:
            if not isinstance(row, dict):
                continue
            self.trades.append(Trade.from_dict(row))

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

    def buy(
        self,
        symbol: str,
        qty: float,
        price: float,
        fee_bps: float,
        note: str = "",
        *,
        decision_ts: datetime | None = None,
        decision_price: float | None = None,
        latency_ms: int | None = None,
        slip_bps: float | None = None,
        impact_bps: float | None = None,
        ts: datetime | None = None,
    ) -> Trade:
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
            existing.cost_basis = float(existing.cost_basis) + cost
            existing.qty = new_qty
        else:
            self.positions[symbol] = Position(
                symbol=symbol, qty=qty, avg_entry=price, cost_basis=cost
            )

        trade = Trade(
            id=str(uuid4()),
            symbol=symbol,
            side="buy",
            qty=qty,
            price=price,
            fee=fee,
            ts=ts or _utc_now(),
            realized_pnl=0.0,
            note=note,
            decision_ts=decision_ts,
            decision_price=decision_price,
            latency_ms=latency_ms,
            slip_bps=slip_bps,
            impact_bps=impact_bps,
            invested_quote=cost,
            meta_complete=True,
        )
        self.trades.append(trade)
        return trade

    def sell(
        self,
        symbol: str,
        qty: float,
        price: float,
        fee_bps: float,
        note: str = "",
        *,
        decision_ts: datetime | None = None,
        decision_price: float | None = None,
        latency_ms: int | None = None,
        slip_bps: float | None = None,
        impact_bps: float | None = None,
        ts: datetime | None = None,
    ) -> Trade:
        if qty <= 0 or price <= 0:
            raise ValueError("qty and price must be positive")
        existing = self.positions.get(symbol)
        if not existing or existing.qty < qty:
            raise ValueError("insufficient position")

        notional = qty * price
        fee = notional * (fee_bps / 10_000.0)
        proceeds = notional - fee
        # Proportional cost basis (entry fees included). Slippage already in ``price``.
        frac = qty / existing.qty if existing.qty > 0 else 0.0
        cost_closed = float(existing.cost_basis) * frac
        if cost_closed <= 0 and existing.avg_entry > 0:
            # Legacy position without cost_basis — incomplete accounting.
            cost_closed = existing.avg_entry * qty
            meta_complete = False
        else:
            meta_complete = True
        realized = proceeds - cost_closed

        avg_ref = existing.avg_entry
        ref_ts = _utc_now()

        existing.qty -= qty
        existing.cost_basis = max(0.0, float(existing.cost_basis) - cost_closed)
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
            ts=ts or _utc_now(),
            realized_pnl=realized,
            note=note,
            decision_ts=decision_ts,
            decision_price=decision_price,
            latency_ms=latency_ms,
            slip_bps=slip_bps,
            impact_bps=impact_bps,
            avg_entry_ref=avg_ref,
            cost_basis_closed=cost_closed,
            reference_ts=ref_ts,
            meta_complete=meta_complete,
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
                    "cost_basis": pos.cost_basis,
                    "mark_price": px,
                    "market_value": pos.market_value(px),
                    "unrealized_pnl": pos.unrealized_pnl(px),
                    "mark_ts": _utc_now().isoformat(),
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
            "exposure_quote": sum(float(p["market_value"]) for p in positions),
            "open_count": len(positions),
        }
