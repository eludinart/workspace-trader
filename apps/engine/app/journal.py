from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DecisionEntry:
    id: str
    ts: datetime
    symbol: str
    action: str
    price: float
    qty: float
    trade_id: str | None
    confidence: float
    features: dict[str, float]
    top_features: list[dict[str, Any]]
    explanation: str
    simulated: bool = False
    source: str = "live"  # live | backtest
    note: str = ""  # entry:signal | exit:signal | exit:tp | exit:sl | exit:kill
    narrative: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts.isoformat(),
            "symbol": self.symbol,
            "action": self.action,
            "price": self.price,
            "qty": self.qty,
            "trade_id": self.trade_id,
            "confidence": self.confidence,
            "features": self.features,
            "top_features": self.top_features,
            "explanation": self.explanation,
            "simulated": self.simulated,
            "source": self.source,
            "note": self.note,
            "narrative": self.narrative,
        }


@dataclass
class DecisionJournal:
    entries: list[DecisionEntry] = field(default_factory=list)
    max_entries: int = 500

    def add(
        self,
        *,
        symbol: str,
        action: str,
        price: float,
        qty: float,
        trade_id: str | None,
        confidence: float,
        features: dict[str, float],
        top_features: list[dict[str, Any]],
        explanation: str,
        simulated: bool = False,
        source: str = "live",
        ts: datetime | None = None,
        note: str = "",
        narrative: str = "",
    ) -> DecisionEntry:
        entry = DecisionEntry(
            id=str(uuid4()),
            ts=ts or _utc_now(),
            symbol=symbol,
            action=action,
            price=price,
            qty=qty,
            trade_id=trade_id,
            confidence=confidence,
            features=features,
            top_features=top_features,
            explanation=explanation,
            simulated=simulated,
            source=source,
            note=note,
            narrative=narrative,
        )
        self.entries.append(entry)
        if len(self.entries) > self.max_entries:
            self.entries = self.entries[-self.max_entries :]
        return entry

    def list(self, limit: int = 50, source: str | None = None) -> list[dict[str, Any]]:
        items = self.entries
        if source:
            items = [e for e in items if e.source == source]
        return [e.to_dict() for e in reversed(items[-limit:])]

    def clear(self) -> None:
        self.entries.clear()

    def load_dicts(self, rows: list[dict[str, Any]]) -> None:
        """Replace journal contents from persisted dict rows (oldest → newest)."""
        loaded: list[DecisionEntry] = []
        for row in rows:
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
            loaded.append(
                DecisionEntry(
                    id=str(row.get("id") or uuid4()),
                    ts=ts,
                    symbol=str(row.get("symbol") or ""),
                    action=str(row.get("action") or "hold"),
                    price=float(row.get("price") or 0),
                    qty=float(row.get("qty") or 0),
                    trade_id=(str(row["trade_id"]) if row.get("trade_id") else None),
                    confidence=float(row.get("confidence") or 0),
                    features=dict(row.get("features") or {}) if isinstance(row.get("features"), dict) else {},
                    top_features=list(row.get("top_features") or [])
                    if isinstance(row.get("top_features"), list)
                    else [],
                    explanation=str(row.get("explanation") or ""),
                    simulated=bool(row.get("simulated")),
                    source=str(row.get("source") or "live"),
                    note=str(row.get("note") or ""),
                    narrative=str(row.get("narrative") or ""),
                )
            )
        self.entries = loaded[-self.max_entries :]
