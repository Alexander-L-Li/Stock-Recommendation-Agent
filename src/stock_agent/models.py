"""Shared data models used across the pipeline.

Plain dataclasses keep the modules decoupled and make tests easy to write.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RedditPost:
    """A Reddit submission or comment normalized into a flat record."""

    id: str
    subreddit: str
    title: str
    body: str
    score: int
    created_utc: float
    url: str = ""
    kind: str = "submission"  # "submission" or "comment"

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}".strip()


@dataclass
class SocialPost:
    """A generic social message (e.g. StockTwits) normalized for the pipeline.

    Shares the ``.text`` contract with :class:`RedditPost` so discovery and
    sentiment aggregation treat all social sources uniformly. ``native_sentiment``
    captures a source-provided label (StockTwits' Bullish/Bearish tags) when
    available, which the aggregator can blend with the text-based score.
    """

    id: str
    source: str  # e.g. "stocktwits", "reddit/stocks"
    title: str
    body: str
    score: int
    created_utc: float
    url: str = ""
    kind: str = ""
    native_sentiment: Optional[str] = None  # "Bullish" | "Bearish" | None

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}".strip()


@dataclass
class NewsArticle:
    """A news article parsed from an RSS feed."""

    title: str
    summary: str
    link: str
    source: str
    published: Optional[datetime] = None

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.summary}".strip()


@dataclass
class NewsRef:
    """A compact news headline reference attached to a pick for the report.

    Lighter than :class:`NewsArticle` (no body) so it's cheap to carry through
    scoring and render in the email.
    """

    title: str
    source: str = ""
    url: str = ""
    published: Optional[datetime] = None

    def sort_key(self) -> float:
        """Recency key for sorting (newest first); undated sorts last."""
        return self.published.timestamp() if self.published else 0.0


@dataclass
class SentimentResult:
    """Aggregated sentiment for a single ticker."""

    ticker: str
    mention_count: int = 0
    avg_sentiment: float = 0.0  # -1.0 .. 1.0
    news_count: int = 0
    avg_news_sentiment: float = 0.0  # -1.0 .. 1.0


@dataclass
class Fundamentals:
    """Key fundamental metrics for a ticker. Any field may be None when missing."""

    ticker: str
    revenue_growth: Optional[float] = None      # fraction, e.g. 0.15 = 15%
    earnings_growth: Optional[float] = None      # fraction
    profit_margin: Optional[float] = None        # fraction
    roe: Optional[float] = None                  # fraction
    debt_to_equity: Optional[float] = None       # ratio (e.g. 1.5)
    free_cash_flow: Optional[float] = None       # absolute, currency
    trailing_pe: Optional[float] = None
    peg_ratio: Optional[float] = None
    price_to_book: Optional[float] = None
    market_cap: Optional[float] = None
    current_price: Optional[float] = None
    target_mean_price: Optional[float] = None
    sector: Optional[str] = None
    name: Optional[str] = None
    error: Optional[str] = None  # populated if fetch failed

    def available_count(self) -> int:
        """Number of non-None scoring metrics (used for data-quality gating)."""
        keys = [
            self.revenue_growth, self.earnings_growth, self.profit_margin,
            self.roe, self.debt_to_equity, self.free_cash_flow,
            self.trailing_pe, self.peg_ratio,
        ]
        return sum(1 for k in keys if k is not None)


@dataclass
class ScoredCandidate:
    """A fully scored candidate ready for ranking and reporting."""

    ticker: str
    final_score: float
    fundamentals_score: float
    sentiment_score: float
    gated: bool = False  # True if hype gate suppressed sentiment contribution
    fundamentals: Optional[Fundamentals] = None
    sentiment: Optional[SentimentResult] = None
    rationale: str = ""
    supporting_signals: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    news: list["NewsRef"] = field(default_factory=list)  # recent headlines
    rank: int = 0
