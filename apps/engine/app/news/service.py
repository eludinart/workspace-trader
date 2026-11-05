"""In-memory news cache and the category brief."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Any

from app.news.feeds import fetch_all
from app.news.models import CATEGORIES, CategoryScore, NewsBrief, NewsCategory, NewsItem, ScoredItem
from app.news.sentiment import score_item

# Crypto is the traded book; macro is the next most relevant driver.
_CATEGORY_WEIGHTS: dict[NewsCategory, float] = {
    "crypto": 0.45,
    "macro": 0.30,
    "world": 0.15,
    "tech": 0.10,
}
_HEADLINE_LIMIT = 10


def _ttl_from_env() -> int:
    raw = os.environ.get("NEWS_TTL_SEC", "600")
    try:
        ttl = int(raw)
    except ValueError:
        return 600
    return max(300, min(ttl, 900))


def bias_label(score: float) -> str:
    if score >= 0.15:
        return "bullish"
    if score <= -0.15:
        return "bearish"
    return "neutral"


def _top_headlines(items: list[ScoredItem], limit: int) -> tuple[ScoredItem, ...]:
    def sort_key(item: ScoredItem) -> tuple[float, float]:
        published = item.published.timestamp() if item.published else 0.0
        return (abs(item.score), published)

    ranked = sorted(items, key=sort_key, reverse=True)
    return tuple(ranked[:limit])


def _brief_labels(headlines: tuple[ScoredItem, ...]) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    for item in headlines:
        for label in item.labels:
            counts[label] = counts.get(label, 0) + 1
    ranked = sorted(counts, key=lambda label: (-counts[label], label))
    return tuple(ranked[:8])


def build_brief(
    items: list[NewsItem],
    *,
    as_of: datetime | None,
    errors: tuple[str, ...] = (),
) -> NewsBrief:
    scored = [score_item(item) for item in items]
    grouped: dict[NewsCategory, list[ScoredItem]] = {category: [] for category in CATEGORIES}
    for item in scored:
        grouped[item.category].append(item)

    scores: dict[str, CategoryScore] = {}
    for category in CATEGORIES:
        group = grouped[category]
        if not group:
            scores[category] = CategoryScore(score=0.0, count=0, label="neutral")
            continue
        average = sum(item.score for item in group) / len(group)
        scores[category] = CategoryScore(
            score=float(average),
            count=len(group),
            label=bias_label(average),
        )

    active = [category for category in CATEGORIES if scores[category].count > 0]
    if not active:
        overall = 0.0
    else:
        weight = sum(_CATEGORY_WEIGHTS[category] for category in active)
        overall = sum(
            scores[category].score * (_CATEGORY_WEIGHTS[category] / weight) for category in active
        )

    headlines = _top_headlines(scored, _HEADLINE_LIMIT)
    return NewsBrief(
        scores=scores,
        overall_bias=float(overall),
        overall_label=bias_label(overall),
        headlines=headlines,
        labels=_brief_labels(headlines),
        as_of=as_of,
        item_count=len(scored),
        errors=errors,
    )


class NewsService:
    """Process-local RSS cache. TTL defaults to 10 minutes (clamped to 5–15)."""

    def __init__(self, ttl_sec: int | None = None) -> None:
        self.ttl_sec = _ttl_from_env() if ttl_sec is None else max(300, min(ttl_sec, 900))
        self._items: list[NewsItem] = []
        self._errors: list[str] = []
        self._fetched_at: datetime | None = None
        self._data_lock = threading.Lock()
        self._refresh_lock = threading.Lock()

    def _is_stale(self) -> bool:
        with self._data_lock:
            fetched = self._fetched_at
        if fetched is None:
            return True
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        return age >= self.ttl_sec

    def refresh_if_stale(self) -> bool:
        """Fetch feeds when the cache is missing or older than the TTL."""
        if not self._is_stale():
            return False
        with self._refresh_lock:
            if not self._is_stale():
                return False
            items, errors = fetch_all()
            with self._data_lock:
                if items or not self._items:
                    self._items = items
                self._errors = errors
                self._fetched_at = datetime.now(timezone.utc)
            return True

    def cached_brief(self) -> NewsBrief:
        with self._data_lock:
            items = list(self._items)
            errors = tuple(self._errors)
            as_of = self._fetched_at
        return build_brief(items, as_of=as_of, errors=errors)

    def brief_dict(self) -> dict[str, Any]:
        return self.cached_brief().to_dict(stale=self._is_stale())

    def brief_payload(self, *, refresh: bool = True) -> dict[str, Any]:
        if refresh:
            self.refresh_if_stale()
        return self.brief_dict()

    def items_payload(
        self,
        *,
        category: NewsCategory | None = None,
        limit: int = 40,
        refresh: bool = True,
    ) -> dict[str, Any]:
        if refresh:
            self.refresh_if_stale()
        with self._data_lock:
            items = list(self._items)
            errors = list(self._errors)
            as_of = self._fetched_at
        if as_of is None:
            stale = True
        else:
            stale = (datetime.now(timezone.utc) - as_of).total_seconds() >= self.ttl_sec
        scored = [score_item(item) for item in items]
        if category is not None:
            scored = [item for item in scored if item.category == category]
        scored.sort(
            key=lambda item: item.published or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        sliced = scored[:limit]
        return {
            "as_of": as_of.isoformat() if as_of else None,
            "count": len(sliced),
            "items": [item.to_dict() for item in sliced],
            "errors": errors,
            "stale": stale,
        }
