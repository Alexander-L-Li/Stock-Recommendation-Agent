"""News collector.

Parses free RSS/Atom feeds (Yahoo Finance, MarketWatch, SEC EDGAR, etc.) into
structured :class:`NewsArticle` records using feedparser. Malformed or empty
feeds are skipped rather than aborting the run. Articles older than the lookback
window are filtered out (when a published date is available).

``feedparser.parse`` accepts a URL, file path, or a raw string/bytes of feed
content, which makes fixture-based testing trivial.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..config import Config
from ..models import NewsArticle

logger = logging.getLogger(__name__)


def _struct_to_datetime(struct_time: Any) -> Optional[datetime]:
    if not struct_time:
        return None
    try:
        return datetime.fromtimestamp(time.mktime(struct_time), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


class NewsCollector:
    def __init__(self, config: Config,
                 parser: Optional[Callable[[str], Any]] = None) -> None:
        self.config = config
        if parser is None:
            import feedparser
            parser = feedparser.parse
        self._parse = parser

    def collect(self, now: Optional[float] = None) -> list[NewsArticle]:
        now = now if now is not None else time.time()
        cutoff = datetime.fromtimestamp(
            now - self.config.lookback_hours * 3600, tz=timezone.utc
        )
        articles: list[NewsArticle] = []
        for feed_url in self.config.news_feeds:
            try:
                articles.extend(self._collect_feed(feed_url, cutoff))
            except Exception as exc:
                logger.warning("Failed to parse feed %s: %s", feed_url, exc)
        return articles

    def _collect_feed(self, feed_url: str, cutoff: datetime) -> list[NewsArticle]:
        parsed = self._parse(feed_url)
        source = ""
        feed_meta = getattr(parsed, "feed", None) or {}
        if isinstance(feed_meta, dict):
            source = feed_meta.get("title", "") or ""
        else:
            source = getattr(feed_meta, "title", "") or ""

        out: list[NewsArticle] = []
        entries = getattr(parsed, "entries", None) or []
        for entry in entries:
            get = entry.get if isinstance(entry, dict) else lambda k, d=None: getattr(entry, k, d)
            published_struct = get("published_parsed") or get("updated_parsed")
            published = _struct_to_datetime(published_struct)
            # If we have a date and it's too old, skip. Undated entries are kept.
            if published is not None and published < cutoff:
                continue
            out.append(
                NewsArticle(
                    title=(get("title") or "").strip(),
                    summary=(get("summary") or get("description") or "").strip(),
                    link=(get("link") or "").strip(),
                    source=source or feed_url,
                    published=published,
                )
            )
        return out
