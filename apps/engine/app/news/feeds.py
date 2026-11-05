"""Fetch and normalize free RSS feeds. Failures are isolated per source."""

from __future__ import annotations

import calendar
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree as ET

import feedparser
import httpx

from app.news.models import NewsCategory, NewsItem
from app.news.sentiment import clean_text

logger = logging.getLogger(__name__)

_MAX_ITEMS_PER_FEED = 12
_MAX_AGE = timedelta(days=3)
_TIMEOUT = httpx.Timeout(8.0, connect=5.0)
_HEADERS = {
    "User-Agent": "workspace-trader/1.0 (paper trading news reader)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}


@dataclass(frozen=True)
class FeedSource:
    source: str
    category: NewsCategory
    url: str


# feeds.reuters.com no longer resolves. Google News RSS is a free, keyless
# feed of Reuters business and markets copy. BBC business fills the same bucket.
_REUTERS_MARKETS = (
    "https://news.google.com/rss/search?q=site:reuters.com+"
    "(markets+OR+business+OR+economy+OR+inflation)&hl=en-US&gl=US&ceid=US:en"
)
_TITLE_SUFFIXES = (" - Reuters", " - BBC News", " - CoinDesk", " - Cointelegraph", " - TechCrunch")

FEEDS: tuple[FeedSource, ...] = (
    FeedSource("coindesk", "crypto", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    FeedSource("cointelegraph", "crypto", "https://cointelegraph.com/rss"),
    FeedSource("reuters_markets", "macro", _REUTERS_MARKETS),
    FeedSource("bbc_business", "macro", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    FeedSource("bbc_world", "world", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    FeedSource("techcrunch", "tech", "https://techcrunch.com/feed/"),
)


class _XmlEntry:
    def __init__(self, title: str, summary: str, link: str, published: str) -> None:
        self.title = title
        self.summary = summary
        self.link = link
        self.published = published
        self.published_parsed = None
        self.updated = ""
        self.updated_parsed = None


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1].lower()
    return tag.lower()


def _parse_xml(content: bytes) -> list[_XmlEntry]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    rows: list[_XmlEntry] = []
    for element in root.iter():
        if _local(element.tag) not in {"item", "entry"}:
            continue
        title = ""
        summary = ""
        link = ""
        published = ""
        for child in list(element):
            tag = _local(child.tag)
            text = (child.text or "").strip()
            if tag == "title" and not title:
                title = text
            elif tag in {"description", "summary", "content"} and not summary:
                summary = text
            elif tag == "link":
                link = text or child.attrib.get("href", "")
            elif tag in {"pubdate", "published", "updated", "date"} and not published:
                published = text
        if title:
            rows.append(_XmlEntry(title, summary, link, published))
    return rows


def _published(entry: Any) -> datetime | None:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed:
        try:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
        except (OverflowError, OSError, ValueError, TypeError):
            return None
    raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if isinstance(raw, str) and raw.strip():
        try:
            stamp = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)
    return None


def _link(entry: Any) -> str:
    link = getattr(entry, "link", None)
    if isinstance(link, str) and link.strip():
        return link.strip()
    links = getattr(entry, "links", None)
    if isinstance(links, list):
        for item in links:
            href = item.get("href") if isinstance(item, dict) else getattr(item, "href", None)
            if isinstance(href, str) and href.strip():
                return href.strip()
    return ""


def _summary(entry: Any) -> str:
    raw = getattr(entry, "summary", None) or getattr(entry, "description", None) or ""
    if not raw:
        content = getattr(entry, "content", None)
        if isinstance(content, list) and content:
            first = content[0]
            raw = first.get("value", "") if isinstance(first, dict) else getattr(first, "value", "")
    return clean_text(str(raw), limit=500)


def _entries(content: bytes) -> list[Any]:
    parsed = feedparser.parse(content)
    entries = list(getattr(parsed, "entries", []) or [])
    if entries:
        return entries
    return list(_parse_xml(content))


def _items_from_feed(feed: FeedSource, content: bytes, now: datetime) -> list[NewsItem]:
    items: list[NewsItem] = []
    for entry in _entries(content):
        title = clean_text(str(getattr(entry, "title", "") or ""), limit=300)
        for suffix in _TITLE_SUFFIXES:
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()
        if not title:
            continue
        published = _published(entry)
        if published is not None and (now - published) > _MAX_AGE:
            continue
        if published is not None and published > now + timedelta(hours=6):
            published = now
        items.append(
            NewsItem(
                title=title,
                summary=_summary(entry),
                link=_link(entry),
                published=published,
                category=feed.category,
                source=feed.source,
            )
        )
    items.sort(
        key=lambda item: item.published or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return items[:_MAX_ITEMS_PER_FEED]


def _fetch_feed(feed: FeedSource) -> tuple[list[NewsItem], str | None]:
    try:
        response = httpx.get(
            feed.url,
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers=_HEADERS,
        )
        response.raise_for_status()
        items = _items_from_feed(feed, response.content, datetime.now(timezone.utc))
    except Exception as exc:  # noqa: BLE001 — one bad feed must not drop the others
        logger.info("news feed %s failed: %s", feed.source, exc)
        return [], f"{feed.source}: {exc}"
    if not items:
        return [], f"{feed.source}: empty feed"
    return items, None


def _dedupe(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    unique: list[NewsItem] = []
    for item in items:
        key = (item.link or item.title).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    unique.sort(
        key=lambda item: item.published or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return unique


def fetch_all() -> tuple[list[NewsItem], list[str]]:
    """Fetch every configured feed in parallel. One failure does not drop the rest."""
    items: list[NewsItem] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(4, len(FEEDS))) as pool:
        for got, err in pool.map(_fetch_feed, FEEDS):
            items.extend(got)
            if err:
                errors.append(err)
    return _dedupe(items), errors
