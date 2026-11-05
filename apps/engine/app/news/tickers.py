"""Extract Coinbase USD product ids mentioned in news headlines."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# Display / alias → base asset on Coinbase (USD pair = BASE-USD).
_ALIAS_TO_BASE: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"\b{pat}\b", re.I), base)
    for pat, base in (
        (r"bitcoin|btc", "BTC"),
        (r"ethereum|ether|\beth\b", "ETH"),
        (r"solana|\bsol\b", "SOL"),
        (r"ripple|\bxrp\b", "XRP"),
        (r"cardano|\bada\b", "ADA"),
        (r"dogecoin|\bdoge\b", "DOGE"),
        (r"avalanche|\bavax\b", "AVAX"),
        (r"chainlink|\blink\b", "LINK"),
        (r"polkadot|\bdot\b", "DOT"),
        (r"polygon|\bmatic\b|\bpol\b", "POL"),
        (r"litecoin|\bltc\b", "LTC"),
        (r"uniswap|\buni\b", "UNI"),
        (r"\baave\b", "AAVE"),
        (r"cosmos|\batom\b", "ATOM"),
        (r"near protocol|\bnear\b", "NEAR"),
        (r"\bsui\b", "SUI"),
        (r"hedera|\bhbar\b", "HBAR"),
        (r"\bondo\b", "ONDO"),
        (r"bittensor|\btao\b", "TAO"),
        (r"\bpepe\b", "PEPE"),
        (r"shiba|\bshib\b", "SHIB"),
        (r"\brender\b|\brndr\b", "RENDER"),
        (r"\bfetch\.?ai\b|\bfet\b", "FET"),
        (r"\binjective\b|\binj\b", "INJ"),
        (r"\bstellar\b|\bxlm\b", "XLM"),
        (r"\btoncoin\b|\bton\b", "TON"),
        (r"\boptimism\b|\bop\b", "OP"),
        (r"\barbitrum\b|\barb\b", "ARB"),
        (r"\bfilecoin\b|\bfil\b", "FIL"),
        (r"\baptos\b|\bapt\b", "APT"),
        (r"\bsei\b", "SEI"),
        (r"\bwif\b|dogwif", "WIF"),
        (r"\bbonk\b", "BONK"),
        (r"hyperliquid|\bhype\b", "HYPE"),
        (r"\bzcash\b|\bzec\b", "ZEC"),
        (r"\bpaxg\b|pax gold", "PAXG"),
        (r"\bmonero\b|\bxmr\b", "XMR"),
    )
)


@dataclass(frozen=True)
class NewsTickerHit:
    symbol: str  # e.g. BTC-USD
    base: str
    score: float
    title: str

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "base": self.base,
            "score": round(self.score, 4),
            "title": self.title,
        }


def extract_bases(text: str) -> list[str]:
    blob = text or ""
    found: list[str] = []
    seen: set[str] = set()
    for pattern, base in _ALIAS_TO_BASE:
        if pattern.search(blob) and base not in seen:
            seen.add(base)
            found.append(base)
    return found


def hits_from_headlines(
    headlines: Iterable[dict | object],
    *,
    min_abs_score: float = 0.2,
    online_usd: set[str] | None = None,
) -> list[NewsTickerHit]:
    """Map scored headlines to tradeable *-USD symbols."""
    out: list[NewsTickerHit] = []
    seen: set[str] = set()
    for item in headlines:
        if isinstance(item, dict):
            title = str(item.get("title") or "")
            summary = str(item.get("summary") or "")
            score = float(item.get("score") or 0.0)
        else:
            title = str(getattr(item, "title", "") or "")
            summary = str(getattr(item, "summary", "") or "")
            score = float(getattr(item, "score", 0.0) or 0.0)
        if abs(score) < min_abs_score:
            continue
        blob = f"{title} {summary}"
        for base in extract_bases(blob):
            symbol = f"{base}-USD"
            if online_usd is not None and symbol not in online_usd:
                continue
            if symbol in seen:
                continue
            seen.add(symbol)
            out.append(NewsTickerHit(symbol=symbol, base=base, score=score, title=title[:160]))
    return out
