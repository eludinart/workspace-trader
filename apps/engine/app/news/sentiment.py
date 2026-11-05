"""Heuristic sentiment for crypto and macro headlines. No API key."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from html import unescape

from app.news.models import NewsItem, ScoredItem

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_NEGATION = re.compile(
    r"\b(no|not|never|without|fails to|failed to|n't)\b(?:\s+\w+){0,3}\s*$"
)
_BTC = re.compile(r"\b(btc|bitcoin)\b")
_ETH = re.compile(r"\b(eth|ethereum)\b")


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern[str]
    impact: float
    label: str


@dataclass(frozen=True)
class Sentiment:
    score: float
    labels: tuple[str, ...]


def _compile(pairs: tuple[tuple[str, float, str], ...]) -> tuple[_Rule, ...]:
    ordered = sorted(pairs, key=lambda item: len(item[0]), reverse=True)
    return tuple(
        _Rule(re.compile(rf"\b{re.escape(phrase)}\b"), impact, label)
        for phrase, impact, label in ordered
    )


# Longer phrases are applied first and masked so single words do not double-count them.
_PHRASES: tuple[tuple[str, float, str], ...] = (
    ("rate cut", 0.70, "rate_cut"),
    ("rate cuts", 0.70, "rate_cut"),
    ("cuts rates", 0.65, "rate_cut"),
    ("cut rates", 0.65, "rate_cut"),
    ("easing cycle", 0.40, "easing"),
    ("quantitative easing", 0.35, "easing"),
    ("soft landing", 0.30, "macro"),
    ("etf approval", 0.90, "etf"),
    ("etf approved", 0.90, "etf"),
    ("spot etf", 0.50, "etf"),
    ("bitcoin etf", 0.45, "etf"),
    ("sec approval", 0.75, "sec"),
    ("sec approves", 0.80, "sec"),
    ("sec approved", 0.80, "sec"),
    ("all-time high", 0.55, "ath"),
    ("all time high", 0.55, "ath"),
    ("record high", 0.40, "ath"),
    ("institutional adoption", 0.50, "adoption"),
    ("net inflow", 0.45, "inflow"),
    ("ceasefire", 0.50, "war"),
    ("peace deal", 0.45, "war"),
    ("inflation cools", 0.50, "inflation"),
    ("inflation slows", 0.45, "inflation"),
    ("inflation eases", 0.45, "inflation"),
    ("cooling inflation", 0.45, "inflation"),
    ("cpi falls", 0.35, "inflation"),
    ("beats estimates", 0.25, "growth"),
    ("better than expected", 0.25, "growth"),
    ("strong demand", 0.30, "demand"),
    ("funds recovered", 0.35, "hack"),
    ("rate hike", -0.70, "rate_hike"),
    ("rate hikes", -0.70, "rate_hike"),
    ("hikes rates", -0.65, "rate_hike"),
    ("higher for longer", -0.45, "rates"),
    ("hot inflation", -0.55, "inflation"),
    ("inflation surge", -0.55, "inflation"),
    ("inflation rises", -0.40, "inflation"),
    ("inflation jumps", -0.45, "inflation"),
    ("sticky inflation", -0.40, "inflation"),
    ("cpi rises", -0.35, "inflation"),
    ("sec charges", -0.80, "sec"),
    ("sec sues", -0.80, "sec"),
    ("sec lawsuit", -0.75, "sec"),
    ("sec probe", -0.55, "sec"),
    ("security breach", -0.75, "hack"),
    ("funds stolen", -0.80, "hack"),
    ("exchange hack", -0.85, "hack"),
    ("bank run", -0.60, "crisis"),
    ("government shutdown", -0.45, "shutdown"),
    ("sell-off", -0.45, "selloff"),
    ("sell off", -0.40, "selloff"),
    ("mass liquidation", -0.60, "liquidation"),
    ("job cuts", -0.30, "layoffs"),
    ("trade war", -0.55, "war"),
    ("military strike", -0.55, "war"),
    ("air strike", -0.50, "war"),
    ("airstrike", -0.50, "war"),
    ("invasion", -0.70, "war"),
    ("recession fears", -0.50, "recession"),
    ("debt default", -0.55, "default"),
)

_WORDS: tuple[tuple[str, float, str], ...] = (
    ("rally", 0.40, "rally"),
    ("rallies", 0.40, "rally"),
    ("surge", 0.35, "surge"),
    ("surges", 0.35, "surge"),
    ("soar", 0.40, "surge"),
    ("soars", 0.40, "surge"),
    ("bullish", 0.45, "bullish"),
    ("breakout", 0.35, "breakout"),
    ("approval", 0.25, "approval"),
    ("approved", 0.30, "approval"),
    ("adoption", 0.30, "adoption"),
    ("inflow", 0.30, "inflow"),
    ("inflows", 0.35, "inflow"),
    ("upgrade", 0.15, "upgrade"),
    ("partnership", 0.20, "partnership"),
    ("recovery", 0.25, "recovery"),
    ("stimulus", 0.30, "stimulus"),
    ("easing", 0.35, "easing"),
    ("dovish", 0.40, "easing"),
    ("rebound", 0.30, "rebound"),
    ("optimism", 0.15, "optimism"),
    ("crash", -0.60, "crash"),
    ("plunge", -0.50, "plunge"),
    ("plunges", -0.50, "plunge"),
    ("bearish", -0.45, "bearish"),
    ("ban", -0.40, "ban"),
    ("banned", -0.40, "ban"),
    ("lawsuit", -0.45, "lawsuit"),
    ("sued", -0.45, "lawsuit"),
    ("indictment", -0.50, "lawsuit"),
    ("sanction", -0.35, "sanctions"),
    ("sanctions", -0.40, "sanctions"),
    ("recession", -0.50, "recession"),
    ("bankruptcy", -0.65, "bankruptcy"),
    ("outflow", -0.35, "outflow"),
    ("outflows", -0.40, "outflow"),
    ("liquidation", -0.45, "liquidation"),
    ("liquidations", -0.50, "liquidation"),
    ("fraud", -0.60, "fraud"),
    ("scam", -0.55, "fraud"),
    ("collapse", -0.60, "collapse"),
    ("shutdown", -0.25, "shutdown"),
    ("layoff", -0.30, "layoffs"),
    ("layoffs", -0.35, "layoffs"),
    ("breach", -0.45, "hack"),
    ("hack", -0.75, "hack"),
    ("hacked", -0.80, "hack"),
    ("exploit", -0.70, "hack"),
    ("exploited", -0.70, "hack"),
    ("war", -0.50, "war"),
    ("halted", -0.30, "halt"),
    ("investigation", -0.25, "investigation"),
    ("crisis", -0.40, "crisis"),
    ("downgrade", -0.35, "downgrade"),
    ("panic", -0.45, "panic"),
    ("etf", 0.25, "etf"),
    ("sec", -0.15, "sec"),
    ("inflation", -0.20, "inflation"),
)

_PHRASE_RULES = _compile(_PHRASES)
_WORD_RULES = _compile(_WORDS)


def clean_text(text: str, *, limit: int | None = None) -> str:
    """Strip markup and collapse whitespace. Case is preserved for display."""
    cleaned = unescape(text or "")
    cleaned = _TAG.sub(" ", cleaned)
    cleaned = cleaned.replace("’", "'")
    cleaned = _WS.sub(" ", cleaned).strip()
    if limit is not None:
        return cleaned[:limit]
    return cleaned


def _norm(text: str) -> str:
    return clean_text(text).lower()


def _is_negated(text: str, start: int) -> bool:
    window = text[max(0, start - 48) : start]
    return _NEGATION.search(window) is not None


def _scan(
    text: str,
    rules: tuple[_Rule, ...],
    scale: float,
    cap: int,
) -> tuple[float, list[str], str]:
    raw = 0.0
    labels: list[str] = []
    spans: list[tuple[int, int]] = []
    for rule in rules:
        hits = 0
        for match in rule.pattern.finditer(text):
            overlaps = any(match.start() < end and match.end() > start for start, end in spans)
            if overlaps:
                continue
            hits += 1
            if hits > cap:
                break
            impact = -rule.impact if _is_negated(text, match.start()) else rule.impact
            raw += impact * scale
            labels.append(rule.label)
            spans.append((match.start(), match.end()))
    if not spans:
        return raw, labels, text
    chars = list(text)
    for start, end in spans:
        for index in range(start, end):
            chars[index] = " "
    return raw, labels, "".join(chars)


def _dedupe(labels: list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for label in labels:
        if label not in seen:
            seen.append(label)
    return tuple(seen)


def _score_field(text: str, scale: float) -> tuple[float, list[str]]:
    phrase_raw, phrase_labels, masked = _scan(text, _PHRASE_RULES, scale, cap=3)
    word_raw, word_labels, _masked = _scan(masked, _WORD_RULES, scale, cap=2)
    return phrase_raw + word_raw, phrase_labels + word_labels


def score_text(title: str, summary: str = "") -> Sentiment:
    """Map a headline to a score in [-1, +1] and the labels that fired."""
    title_n = _norm(title)
    summary_n = _norm(summary)
    title_raw, title_labels = _score_field(title_n, scale=1.6)
    summary_raw, summary_labels = _score_field(summary_n, scale=1.0)
    labels = title_labels + summary_labels
    blob = f"{title_n} {summary_n}"
    if _BTC.search(blob):
        labels.append("btc")
    if _ETH.search(blob):
        labels.append("eth")
    score = math.tanh(title_raw + summary_raw)
    return Sentiment(score=float(score), labels=_dedupe(labels))


def score_item(item: NewsItem) -> ScoredItem:
    sentiment = score_text(item.title, item.summary)
    return ScoredItem(
        title=item.title,
        summary=item.summary,
        link=item.link,
        published=item.published,
        category=item.category,
        source=item.source,
        score=sentiment.score,
        labels=sentiment.labels,
    )
