"""Sentiment analysis interface + per-ticker aggregation.

``SentimentAnalyzer`` is the pluggable contract: a single ``score(text) -> float``
in ``[-1, 1]``. The MVP implementation is VADER; FinBERT can drop in behind the
same interface later without touching the aggregation logic.

``SentimentAggregator`` ties an analyzer together with the ticker extractor to
produce per-ticker :class:`SentimentResult` records (mention counts + average
sentiment) for both Reddit and news text.
"""
from __future__ import annotations

from typing import Iterable, Optional, Protocol

from ..extraction.ticker_extractor import TickerExtractor
from ..models import NewsArticle, RedditPost, SentimentResult


class SentimentAnalyzer(Protocol):
    """Pluggable sentiment backend. Returns a compound score in [-1, 1]."""

    def score(self, text: str) -> float:
        ...


class SentimentAggregator:
    def __init__(self, analyzer: SentimentAnalyzer,
                 extractor: Optional[TickerExtractor] = None) -> None:
        self.analyzer = analyzer
        self.extractor = extractor or TickerExtractor()

    def aggregate(
        self,
        social_posts: Iterable[object],
        news_articles: Iterable[NewsArticle],
        candidates: Optional[Iterable[str]] = None,
    ) -> dict[str, SentimentResult]:
        """Compute per-ticker sentiment from social posts (Reddit, StockTwits,
        ...) and news.

        Any object exposing a ``.text`` property is accepted as a social post; an
        optional ``.native_sentiment`` ("Bullish"/"Bearish") is blended with the
        text-based score when present. If ``candidates`` is given, only those
        tickers are tracked; otherwise every extracted ticker is tracked.
        """
        allow = {t.upper() for t in candidates} if candidates is not None else None
        results: dict[str, SentimentResult] = {}

        def _ensure(ticker: str) -> SentimentResult:
            if ticker not in results:
                results[ticker] = SentimentResult(ticker=ticker)
            return results[ticker]

        # Social (Reddit + StockTwits + any .text source)
        for post in social_posts:
            text = getattr(post, "text", "") or ""
            if not text:
                continue
            tickers = self.extractor.extract_from_text(text)
            if not tickers:
                continue
            s = self._blended_score(text, getattr(post, "native_sentiment", None))
            for t in tickers:
                if allow is not None and t not in allow:
                    continue
                r = _ensure(t)
                # incremental running mean
                r.avg_sentiment = (
                    (r.avg_sentiment * r.mention_count) + s
                ) / (r.mention_count + 1)
                r.mention_count += 1

        # News
        for article in news_articles:
            text = article.text
            if not text:
                continue
            tickers = self.extractor.extract_from_text(text)
            if not tickers:
                continue
            s = self.analyzer.score(text)
            for t in tickers:
                if allow is not None and t not in allow:
                    continue
                r = _ensure(t)
                r.avg_news_sentiment = (
                    (r.avg_news_sentiment * r.news_count) + s
                ) / (r.news_count + 1)
                r.news_count += 1

        return results

    def _blended_score(self, text: str, native_sentiment: Optional[str]) -> float:
        """Text-based score, optionally blended 50/50 with a source-provided
        Bullish/Bearish label (a strong, explicit signal)."""
        s = self.analyzer.score(text)
        if native_sentiment == "Bullish":
            return max(-1.0, min(1.0, 0.5 * s + 0.5 * 0.6))
        if native_sentiment == "Bearish":
            return max(-1.0, min(1.0, 0.5 * s + 0.5 * -0.6))
        return s
