"""Configuration for the stock agent.

All tunables live here with sensible defaults. Anything environment-specific
(secrets, table name, email addresses) is read from environment variables so the
same code runs locally and in Lambda without changes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    """Central configuration object. Construct with ``Config.from_env()``."""

    # --- AWS / storage ---
    table_name: str = "stock-agent"
    aws_region: str = "us-east-1"

    # --- Email (SES) ---
    sender_email: str = ""
    recipient_emails: list[str] = field(default_factory=list)
    error_email: str = ""  # where to send failure alerts (defaults to recipient[0])

    # --- Reddit ---
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "stock-agent/0.1 by personal-use"
    subreddits: list[str] = field(
        default_factory=lambda: [
            "stocks", "investing", "ValueInvesting", "StockMarket",
        ]
    )
    reddit_post_limit: int = 50           # posts pulled per subreddit
    reddit_comment_limit: int = 10        # top comments scanned per post
    lookback_hours: int = 24              # discovery window
    enable_reddit: bool = True            # Reddit collector on/off

    # --- StockTwits (no auth required) ---
    enable_stocktwits: bool = True
    stocktwits_symbol_limit: int = 20     # max symbols (trending+watchlist) fetched

    # --- News (RSS) ---
    news_feeds: list[str] = field(
        default_factory=lambda: [
            "https://finance.yahoo.com/news/rssindex",
            "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        ]
    )

    # --- Discovery / universe ---
    max_candidates: int = 40       # cap on tickers scored per run (cost/time guard)
    min_mentions: int = 1          # minimum mentions to be a discovery candidate
    # #5: analyze a fixed reference index (S&P 500) instead of only what social
    # media mentions; social becomes a prioritization overlay, not the gate.
    # The per-run cap still bounds cost; coverage rotates by date.
    enable_fixed_universe: bool = True

    # --- Scoring weights (must sum to 1.0) ---
    fundamentals_weight: float = 0.70
    sentiment_weight: float = 0.30

    # --- #3: sector-relative cross-sectional scoring ---
    # Score growth/quality metrics by percentile rank within a candidate's GICS
    # sector instead of fixed linear thresholds. Falls back to absolute scaling
    # when a sector cohort has fewer than ``sector_min_peers`` members.
    enable_sector_relative: bool = True
    sector_min_peers: int = 4

    # --- #4: price-based risk/momentum factors ---
    # Apply a bounded risk tilt (+/- ``risk_tilt_max`` points) to the final score
    # from momentum/volatility/drawdown, and flag thin liquidity.
    enable_price_factors: bool = True
    risk_tilt_max: float = 10.0
    min_dollar_volume: float = 2_000_000.0  # liquidity floor ($/day)
    price_benchmark: str = "SPY"

    # --- Hype gate ---
    # A stock must clear this normalized fundamentals score (0-100) before
    # positive sentiment is allowed to lift its final score.
    hype_gate_min_fundamentals: float = 40.0
    # Candidates with fewer than this many available fundamental metrics are
    # treated as "insufficient data" and excluded from the ranked picks.
    min_fundamental_metrics: int = 3

    # --- Report ---
    top_n: int = 10  # number of picks to include in the emailed report

    # --- Holdings (portfolio tracker) ---
    # When enabled, tickers on the holdings list are always fetched, scored, and
    # rendered in a dedicated "Your Holdings" section of the report regardless of
    # rank, so the owner can track sentiment/news/risk and buy/sell signals.
    enable_holdings: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        recipients = _env_list("RECIPIENT_EMAILS", [])
        cfg = cls(
            table_name=os.environ.get("TABLE_NAME", cls.table_name),
            aws_region=os.environ.get("AWS_REGION", cls.aws_region),
            sender_email=os.environ.get("SENDER_EMAIL", ""),
            recipient_emails=recipients,
            error_email=os.environ.get("ERROR_EMAIL", ""),
            reddit_client_id=os.environ.get("REDDIT_CLIENT_ID", ""),
            reddit_client_secret=os.environ.get("REDDIT_CLIENT_SECRET", ""),
            reddit_user_agent=os.environ.get(
                "REDDIT_USER_AGENT", cls.reddit_user_agent
            ),
            subreddits=_env_list("SUBREDDITS", cls().subreddits),
            reddit_post_limit=_env_int("REDDIT_POST_LIMIT", cls.reddit_post_limit),
            reddit_comment_limit=_env_int(
                "REDDIT_COMMENT_LIMIT", cls.reddit_comment_limit
            ),
            lookback_hours=_env_int("LOOKBACK_HOURS", cls.lookback_hours),
            enable_reddit=_env_bool("ENABLE_REDDIT", cls.enable_reddit),
            enable_stocktwits=_env_bool(
                "ENABLE_STOCKTWITS", cls.enable_stocktwits
            ),
            stocktwits_symbol_limit=_env_int(
                "STOCKTWITS_SYMBOL_LIMIT", cls.stocktwits_symbol_limit
            ),
            news_feeds=_env_list("NEWS_FEEDS", cls().news_feeds),
            max_candidates=_env_int("MAX_CANDIDATES", cls.max_candidates),
            min_mentions=_env_int("MIN_MENTIONS", cls.min_mentions),
            enable_fixed_universe=_env_bool(
                "ENABLE_FIXED_UNIVERSE", cls.enable_fixed_universe
            ),
            fundamentals_weight=_env_float(
                "FUNDAMENTALS_WEIGHT", cls.fundamentals_weight
            ),
            sentiment_weight=_env_float("SENTIMENT_WEIGHT", cls.sentiment_weight),
            enable_sector_relative=_env_bool(
                "ENABLE_SECTOR_RELATIVE", cls.enable_sector_relative
            ),
            sector_min_peers=_env_int("SECTOR_MIN_PEERS", cls.sector_min_peers),
            enable_price_factors=_env_bool(
                "ENABLE_PRICE_FACTORS", cls.enable_price_factors
            ),
            risk_tilt_max=_env_float("RISK_TILT_MAX", cls.risk_tilt_max),
            min_dollar_volume=_env_float(
                "MIN_DOLLAR_VOLUME", cls.min_dollar_volume
            ),
            price_benchmark=os.environ.get("PRICE_BENCHMARK", cls.price_benchmark),
            hype_gate_min_fundamentals=_env_float(
                "HYPE_GATE_MIN_FUNDAMENTALS", cls.hype_gate_min_fundamentals
            ),
            min_fundamental_metrics=_env_int(
                "MIN_FUNDAMENTAL_METRICS", cls.min_fundamental_metrics
            ),
            top_n=_env_int("TOP_N", cls.top_n),
            enable_holdings=_env_bool("ENABLE_HOLDINGS", cls.enable_holdings),
        )
        if not cfg.error_email and cfg.recipient_emails:
            cfg.error_email = cfg.recipient_emails[0]
        cfg.validate()
        return cfg

    def validate(self) -> None:
        total = self.fundamentals_weight + self.sentiment_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Scoring weights must sum to 1.0, got {total:.3f} "
                f"(fundamentals={self.fundamentals_weight}, "
                f"sentiment={self.sentiment_weight})"
            )
        if not self.subreddits:
            raise ValueError("At least one subreddit must be configured")
        if not (0 <= self.hype_gate_min_fundamentals <= 100):
            raise ValueError("hype_gate_min_fundamentals must be in 0..100")
