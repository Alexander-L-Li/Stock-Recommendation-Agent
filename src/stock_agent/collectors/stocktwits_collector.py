"""StockTwits collector.

StockTwits is a finance-focused social network whose public v2 API requires no
authentication for read endpoints — a useful, approval-free social-sentiment
source (especially while Reddit API access is gated). It complements Reddit:
when Reddit credentials are available, both feed the same discovery + sentiment
aggregation as a combined "social" signal.

Two endpoints are used:
  * trending symbols   -> discovery of currently active tickers
  * per-symbol stream  -> recent messages for sentiment + mention volume

Messages are normalized into :class:`SocialPost` records (the same shape the
Reddit collector emits), so everything downstream treats Reddit and StockTwits
uniformly. The cashtag is guaranteed present in the post text so ticker
extraction attributes each message to the right symbol.

HTTP is done with the standard library (``urllib``) to avoid adding a dependency
(keeps the Lambda package lean). The ``http_get`` callable is injectable so
tests run without network access.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..config import Config
from ..models import SocialPost

logger = logging.getLogger(__name__)

_TRENDING_URL = "https://api.stocktwits.com/api/2/trending/symbols.json"
_STREAM_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"


def _default_http_get(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "stock-agent/0.1 (personal research)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _parse_created_at(value: Any) -> Optional[float]:
    """StockTwits timestamps look like '2026-06-25T16:37:00Z'."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


class StockTwitsCollector:
    def __init__(self, config: Config,
                 http_get: Optional[Callable[[str], dict]] = None,
                 request_pause: float = 0.0) -> None:
        self.config = config
        self._get = http_get or _default_http_get
        # Optional politeness delay between requests (kept 0 in tests).
        self._pause = request_pause

    def collect(self, watchlist: Optional[list[str]] = None,
                now: Optional[float] = None) -> list[SocialPost]:
        now = now if now is not None else time.time()
        cutoff = now - self.config.lookback_hours * 3600

        symbols = self._select_symbols(watchlist or [])
        posts: list[SocialPost] = []
        for symbol in symbols:
            try:
                posts.extend(self._collect_symbol(symbol, cutoff))
            except Exception as exc:  # one bad symbol shouldn't abort the run
                logger.warning("StockTwits fetch failed for %s: %s", symbol, exc)
            if self._pause:
                time.sleep(self._pause)
        return posts

    def _select_symbols(self, watchlist: list[str]) -> list[str]:
        """Trending symbols ∪ watchlist, capped for rate/time safety."""
        trending: list[str] = []
        try:
            data = self._get(_TRENDING_URL)
            trending = [s.get("symbol", "").upper()
                        for s in data.get("symbols", []) if s.get("symbol")]
        except Exception as exc:
            logger.warning("StockTwits trending fetch failed: %s", exc)

        ordered: list[str] = []
        seen: set[str] = set()
        for sym in trending + [w.strip().upper() for w in watchlist]:
            # Skip StockTwits crypto symbols (e.g. BTC.X) — equities only.
            if sym and sym not in seen and not sym.endswith(".X"):
                seen.add(sym)
                ordered.append(sym)
        return ordered[: self.config.stocktwits_symbol_limit]

    def _collect_symbol(self, symbol: str, cutoff: float) -> list[SocialPost]:
        data = self._get(_STREAM_URL.format(symbol=symbol))
        out: list[SocialPost] = []
        for msg in data.get("messages", []):
            created = _parse_created_at(msg.get("created_at"))
            if created is not None and created < cutoff:
                continue
            body = (msg.get("body") or "").strip()
            # Guarantee the cashtag is present so extraction attributes it.
            if f"${symbol}" not in body.upper():
                body = f"${symbol} {body}"
            likes = 0
            if isinstance(msg.get("likes"), dict):
                likes = int(msg["likes"].get("total", 0) or 0)
            out.append(
                SocialPost(
                    id=f"st-{msg.get('id', '')}",
                    source="stocktwits",
                    title="",
                    body=body,
                    score=likes,
                    created_utc=created or 0.0,
                    url=f"https://stocktwits.com/symbol/{symbol}",
                    kind="stocktwits",
                    native_sentiment=self._native_sentiment(msg),
                )
            )
        return out

    @staticmethod
    def _native_sentiment(msg: dict) -> Optional[str]:
        """StockTwits users can tag a message Bullish/Bearish."""
        entities = msg.get("entities") or {}
        sentiment = entities.get("sentiment") or {}
        if isinstance(sentiment, dict):
            basic = sentiment.get("basic")
            if basic in ("Bullish", "Bearish"):
                return basic
        return None
