"""Per-ticker recent news, best-effort, via yfinance ``Ticker.news``.

This enriches the email so each pick carries a couple of *stock-specific*
headlines (the RSS feeds give broad market news that rarely names a specific
ticker). yfinance is unofficial and its news payload shape changes between
versions, so every access is defensive and failures degrade to "no news" rather
than breaking the run. The yfinance factory is injectable for testing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..models import NewsRef

logger = logging.getLogger(__name__)


def _default_factory(symbol: str) -> Any:
    import yfinance as yf

    return yf.Ticker(symbol)


def _coerce_published(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    # Epoch seconds (older yfinance: providerPublishTime).
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    # ISO-8601 string (newer yfinance: content.pubDate).
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _parse_item(item: dict) -> Optional[NewsRef]:
    """Handle both the legacy flat shape and the newer ``content`` shape."""
    if not isinstance(item, dict):
        return None
    content = item.get("content") if isinstance(item.get("content"), dict) else None
    if content:
        title = (content.get("title") or "").strip()
        # URL can live under canonicalUrl/clickThroughUrl.
        url = ""
        for key in ("canonicalUrl", "clickThroughUrl"):
            sub = content.get(key)
            if isinstance(sub, dict) and sub.get("url"):
                url = sub["url"]
                break
        provider = content.get("provider") or {}
        source = (provider.get("displayName") if isinstance(provider, dict)
                  else "") or ""
        published = _coerce_published(content.get("pubDate")
                                      or content.get("displayTime"))
    else:
        title = (item.get("title") or "").strip()
        url = item.get("link") or ""
        source = item.get("publisher") or ""
        published = _coerce_published(item.get("providerPublishTime"))
    if not title:
        return None
    return NewsRef(title=title, source=source, url=url, published=published)


class YFinanceNewsProvider:
    def __init__(self, ticker_factory: Optional[Callable[[str], Any]] = None,
                 limit: int = 3) -> None:
        self._factory = ticker_factory or _default_factory
        self.limit = limit

    def recent(self, ticker: str, limit: Optional[int] = None) -> list[NewsRef]:
        limit = limit or self.limit
        try:
            obj = self._factory(ticker)
            raw = getattr(obj, "news", None) or []
        except Exception as exc:  # network/parse failures are non-fatal
            logger.warning("News fetch failed for %s: %s", ticker, exc)
            return []
        refs: list[NewsRef] = []
        for item in raw:
            ref = _parse_item(item)
            if ref is not None:
                refs.append(ref)
        refs.sort(key=lambda r: r.sort_key(), reverse=True)
        return refs[:limit]
