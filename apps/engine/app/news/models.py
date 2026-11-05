"""Normalized news items and the aggregated brief."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

NewsCategory = Literal["crypto", "macro", "tech", "world"]

CATEGORIES: tuple[NewsCategory, ...] = ("crypto", "macro", "tech", "world")


@dataclass(frozen=True)
class NewsItem:
    title: str
    summary: str
    link: str
    published: datetime | None
    category: NewsCategory
    source: str


@dataclass(frozen=True)
class ScoredItem:
    title: str
    summary: str
    link: str
    published: datetime | None
    category: NewsCategory
    source: str
    score: float
    labels: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "link": self.link,
            "published": self.published.isoformat() if self.published else None,
            "category": self.category,
            "source": self.source,
            "score": round(self.score, 4),
            "labels": list(self.labels),
        }


@dataclass(frozen=True)
class CategoryScore:
    score: float
    count: int
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "count": self.count,
            "label": self.label,
        }


@dataclass(frozen=True)
class NewsBrief:
    """Category scores, overall bias, and the strongest recent headlines."""

    scores: dict[str, CategoryScore]
    overall_bias: float
    overall_label: str
    headlines: tuple[ScoredItem, ...]
    labels: tuple[str, ...]
    as_of: datetime | None
    item_count: int
    errors: tuple[str, ...]

    def feature_vector(self) -> dict[str, float]:
        """Decision-time features. These are not XGBoost training columns."""
        return {
            "news_crypto": float(self.scores["crypto"].score),
            "news_macro": float(self.scores["macro"].score),
            "news_tech": float(self.scores["tech"].score),
            "news_world": float(self.scores["world"].score),
            "news_bias": float(self.overall_bias),
        }

    def to_dict(self, *, stale: bool = False) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "overall_bias": round(self.overall_bias, 4),
            "overall_label": self.overall_label,
            "scores": {name: score.to_dict() for name, score in self.scores.items()},
            "labels": list(self.labels),
            "headlines": [item.to_dict() for item in self.headlines],
            "item_count": self.item_count,
            "errors": list(self.errors),
            "stale": stale,
        }
