"""Auto-build a liquid Coinbase USD spot universe ranked by volume + news boosts."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.market.coinbase import CoinbaseClient
from app.news.tickers import NewsTickerHit, hits_from_headlines

logger = logging.getLogger(__name__)

_STABLE_BASES = {
    "USD",
    "USDT",
    "USDC",
    "DAI",
    "EURC",
    "EUR",
    "GBP",
    "PYUSD",
    "USDE",
    "FDUSD",
    "TUSD",
    "GUSD",
    "PAX",
    "USDP",
}
_SKIP_BASES = {"WBTC", "CBETH", "WETH", "STETH", "TBTC", "CBBTC"}


@dataclass(frozen=True)
class UniverseRow:
    symbol: str
    base: str
    volume_24h: float
    rank: int
    source: str = "volume"  # volume | news | core

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "base": self.base,
            "volume_24h": self.volume_24h,
            "rank": self.rank,
            "source": self.source,
        }


@dataclass
class _NewsBoost:
    symbol: str
    score: float
    title: str
    expires_at: datetime


@dataclass
class UniverseService:
    """Refresh a volume-ranked USD spot book; merge news-mentioned symbols."""

    market: CoinbaseClient
    size: int = 50
    min_volume_usd: float = 1_000_000.0
    refresh_hours: float = 6.0
    core: list[str] = field(default_factory=lambda: ["BTC-USD", "ETH-USD", "SOL-USD"])
    news_ttl_hours: float = 12.0
    rows: list[UniverseRow] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    updated_at: datetime | None = None
    last_error: str | None = None
    online_usd: set[str] = field(default_factory=set)
    _news_boosts: dict[str, _NewsBoost] = field(default_factory=dict)
    news_hits: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.size = max(10, min(80, int(self.size)))
        self.min_volume_usd = max(0.0, float(self.min_volume_usd))
        self.refresh_hours = max(1.0, float(self.refresh_hours))
        self.core = [s.strip().upper() for s in self.core if str(s).strip()]
        if not self.symbols:
            self.symbols = list(self.core)

    def stale(self) -> bool:
        if self.updated_at is None or not self.rows:
            return True
        age_h = (datetime.now(timezone.utc) - self.updated_at).total_seconds() / 3600.0
        return age_h >= self.refresh_hours

    def refresh(self, force: bool = False) -> list[str]:
        if not force and not self.stale():
            return self._merge_symbols()
        try:
            products = self.market.get_products()
            volumes = self.market.get_volume_summary()
            built = self._build(products, volumes)
            if not built:
                raise RuntimeError("empty universe after filters")
            self.rows = built
            self.updated_at = datetime.now(timezone.utc)
            self.last_error = None
            logger.info(
                "universe refreshed: %d volume symbols (top=%s)",
                len(self.rows),
                [r.symbol for r in self.rows[:5]],
            )
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            logger.warning("universe refresh failed: %s", exc)
            if not self.rows:
                self.rows = [
                    UniverseRow(symbol=s, base=s.split("-")[0], volume_24h=0.0, rank=i, source="core")
                    for i, s in enumerate(self.core, start=1)
                ]
        return self._merge_symbols()

    def ingest_news(self, headlines: list[dict[str, Any]] | list[Any]) -> list[str]:
        """Add / refresh news-mentioned USD pairs into the active book."""
        self._expire_news()
        hits = hits_from_headlines(
            headlines,
            min_abs_score=0.2,
            online_usd=self.online_usd or None,
        )
        now = datetime.now(timezone.utc)
        ttl = timedelta(hours=self.news_ttl_hours)
        added: list[str] = []
        for hit in hits:
            # If we have no product catalog yet, still accept common USD pairs.
            if self.online_usd and hit.symbol not in self.online_usd:
                continue
            prev = self._news_boosts.get(hit.symbol)
            self._news_boosts[hit.symbol] = _NewsBoost(
                symbol=hit.symbol,
                score=hit.score,
                title=hit.title,
                expires_at=now + ttl,
            )
            if prev is None:
                added.append(hit.symbol)
        self.news_hits = [
            {
                "symbol": b.symbol,
                "score": b.score,
                "title": b.title,
                "expires_at": b.expires_at.isoformat(),
            }
            for b in sorted(self._news_boosts.values(), key=lambda x: abs(x.score), reverse=True)
        ]
        self._merge_symbols()
        if added:
            logger.info("news universe boosts: %s", added)
        return added

    def snapshot(self, signals: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        sig = signals or {}
        rows_out: list[dict[str, Any]] = []
        for r in self.rows:
            row = r.to_dict()
            row["signal"] = sig.get(r.symbol)
            row["from_news"] = r.symbol in self._news_boosts
            rows_out.append(row)
        # Include news-only symbols not in volume top-N.
        for boost in self._news_boosts.values():
            if any(r["symbol"] == boost.symbol for r in rows_out):
                continue
            rows_out.append(
                {
                    "symbol": boost.symbol,
                    "base": boost.symbol.split("-")[0],
                    "volume_24h": 0.0,
                    "rank": 0,
                    "source": "news",
                    "signal": sig.get(boost.symbol),
                    "from_news": True,
                    "news_score": boost.score,
                    "news_title": boost.title,
                }
            )
        return {
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "size": len(self.symbols),
            "min_volume_usd": self.min_volume_usd,
            "refresh_hours": self.refresh_hours,
            "error": self.last_error,
            "symbols": list(self.symbols),
            "rows": rows_out,
            "news_hits": list(self.news_hits),
        }

    def _expire_news(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [k for k, v in self._news_boosts.items() if v.expires_at <= now]
        for k in expired:
            self._news_boosts.pop(k, None)

    def _merge_symbols(self) -> list[str]:
        self._expire_news()
        ordered: list[str] = []
        seen: set[str] = set()
        for core in self.core:
            if core not in seen:
                ordered.append(core)
                seen.add(core)
        # News boosts first after core so they get scanned sooner.
        for sym in sorted(self._news_boosts, key=lambda s: abs(self._news_boosts[s].score), reverse=True):
            if sym not in seen:
                ordered.append(sym)
                seen.add(sym)
        for row in self.rows:
            if row.symbol not in seen:
                ordered.append(row.symbol)
                seen.add(row.symbol)
            if len(ordered) >= self.size + len(self._news_boosts):
                # Keep full volume book up to size, news can overflow slightly.
                if len(ordered) >= self.size + max(5, len(self._news_boosts)):
                    break
        # Cap hard book size.
        hard = self.size + 10
        self.symbols = ordered[:hard]
        return list(self.symbols)

    def _build(
        self, products: list[dict[str, Any]], volumes: list[dict[str, Any]]
    ) -> list[UniverseRow]:
        online_usd: dict[str, str] = {}
        for p in products:
            pid = str(p.get("id") or "").upper()
            if not pid.endswith("-USD"):
                continue
            if str(p.get("status") or "").lower() != "online":
                continue
            if p.get("trading_disabled") is True:
                continue
            base = str(p.get("base_currency") or pid.split("-")[0]).upper()
            if base in _STABLE_BASES or base in _SKIP_BASES:
                continue
            quote = str(p.get("quote_currency") or "USD").upper()
            if quote != "USD":
                continue
            online_usd[pid] = base
        self.online_usd = set(online_usd)

        vol_map: dict[str, float] = {}
        for item in volumes:
            pid = str(item.get("id") or "").upper()
            if pid not in online_usd:
                continue
            raw = item.get("spot_volume_24hour") or item.get("volume_24h") or 0
            try:
                vol = float(raw or 0)
            except (TypeError, ValueError):
                vol = 0.0
            if vol < self.min_volume_usd:
                continue
            vol_map[pid] = vol

        ranked = sorted(vol_map.items(), key=lambda kv: kv[1], reverse=True)
        picked: list[tuple[str, float, str]] = []
        seen: set[str] = set()
        for core in self.core:
            if core in online_usd and core not in seen:
                picked.append((core, vol_map.get(core, 0.0), "core"))
                seen.add(core)
        for pid, vol in ranked:
            if pid in seen:
                continue
            picked.append((pid, vol, "volume"))
            seen.add(pid)
            if len(picked) >= self.size:
                break

        rows: list[UniverseRow] = []
        for i, (pid, vol, source) in enumerate(picked, start=1):
            rows.append(
                UniverseRow(
                    symbol=pid,
                    base=online_usd.get(pid, pid.split("-")[0]),
                    volume_24h=vol,
                    rank=i,
                    source=source,
                )
            )
        return rows
