"""Proactive multi-category news tracking (RSS + heuristic sentiment)."""

from app.news.models import NewsBrief, NewsItem
from app.news.service import NewsService

__all__ = ["NewsBrief", "NewsItem", "NewsService"]
