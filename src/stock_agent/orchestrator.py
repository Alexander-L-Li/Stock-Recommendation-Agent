"""Pipeline orchestrator.

Wires the stages together:

  collectors (Reddit + news)
    -> ticker extraction / discovery (+ watchlist)
    -> sentiment aggregation
    -> fundamentals fetch
    -> scoring (70/30 + hype gate)
    -> report build
    -> email send + history persist

Every collaborator is injectable so the whole pipeline can run end-to-end in
tests with fakes and no network/AWS. ``Orchestrator.build_default`` constructs
the real components from :class:`Config`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from .config import Config
from .extraction.ticker_extractor import TickerExtractor
from .report.builder import Report, ReportBuilder
from .scoring.engine import ScoringEngine
from .sentiment.base import SentimentAggregator

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    run_date: str
    report: Report
    ranked_count: int
    excluded_count: int
    candidates: list[str]
    message_id: Optional[str] = None
    emailed: bool = False


class Orchestrator:
    def __init__(
        self,
        config: Config,
        reddit_collector: Any,
        news_collector: Any,
        fundamentals_fetcher: Any,
        store: Any,
        stocktwits_collector: Any = None,
        sentiment_aggregator: Optional[SentimentAggregator] = None,
        extractor: Optional[TickerExtractor] = None,
        scoring_engine: Optional[ScoringEngine] = None,
        report_builder: Optional[ReportBuilder] = None,
        email_sender: Any = None,
    ) -> None:
        self.config = config
        self.reddit = reddit_collector
        self.news = news_collector
        self.stocktwits = stocktwits_collector
        self.fundamentals = fundamentals_fetcher
        self.store = store
        self.extractor = extractor or TickerExtractor()
        self.sentiment = sentiment_aggregator
        self.engine = scoring_engine or ScoringEngine(config)
        self.report_builder = report_builder or ReportBuilder(top_n=config.top_n)
        self.email = email_sender

    @classmethod
    def build_default(cls, config: Config, store: Any = None) -> "Orchestrator":
        """Construct the production wiring with real collaborators."""
        from .collectors.news_collector import NewsCollector
        from .collectors.reddit_collector import RedditCollector
        from .collectors.stocktwits_collector import StockTwitsCollector
        from .delivery.ses_sender import SesEmailSender
        from .fundamentals.yfinance_fetcher import FundamentalsFetcher
        from .sentiment.vader import VaderAnalyzer
        from .storage.dynamo import Store

        store = store or Store(config.table_name, region=config.aws_region)
        extractor = TickerExtractor()
        reddit = RedditCollector(config) if config.enable_reddit else None
        stocktwits = (StockTwitsCollector(config)
                      if config.enable_stocktwits else None)
        return cls(
            config=config,
            reddit_collector=reddit,
            news_collector=NewsCollector(config),
            stocktwits_collector=stocktwits,
            fundamentals_fetcher=FundamentalsFetcher(),
            store=store,
            sentiment_aggregator=SentimentAggregator(VaderAnalyzer(), extractor),
            extractor=extractor,
            scoring_engine=ScoringEngine(config),
            report_builder=ReportBuilder(top_n=config.top_n),
            email_sender=SesEmailSender(config, store_region_client(config)),
        )

    def run(self, run_date: Optional[str] = None, send_email: bool = True,
            persist: bool = True) -> RunResult:
        run_date = run_date or date.today().isoformat()
        logger.info("Starting run for %s", run_date)

        # 1. Watchlist (drives both discovery and StockTwits symbol selection)
        watchlist = self.store.list_watchlist() if self.store else []

        # 2. Collect social sources (Reddit + StockTwits) and news.
        reddit_posts = self.reddit.collect() if self.reddit is not None else []
        stocktwits_posts = (
            self.stocktwits.collect(watchlist=watchlist)
            if self.stocktwits is not None else []
        )
        social_posts = list(reddit_posts) + list(stocktwits_posts)
        articles = self.news.collect()
        logger.info("Collected %d reddit + %d stocktwits social posts, "
                    "%d news articles", len(reddit_posts),
                    len(stocktwits_posts), len(articles))

        # 3. Discover candidate universe (social + news + watchlist)
        candidates, _counts = self.extractor.discover(
            social_posts, articles, watchlist=watchlist,
            min_mentions=self.config.min_mentions,
            max_candidates=self.config.max_candidates,
        )
        logger.info("Discovered %d candidates (%d from watchlist)",
                    len(candidates), len(watchlist))

        # 4. Sentiment (Reddit + StockTwits aggregate as one social signal)
        sentiment = {}
        if self.sentiment is not None:
            sentiment = self.sentiment.aggregate(social_posts, articles, candidates)

        # 5. Fundamentals
        fundamentals = self.fundamentals.fetch_many(candidates)

        # 6. Score + rank
        ranked, excluded = self.engine.rank(fundamentals, sentiment)
        logger.info("Ranked %d, excluded %d", len(ranked), len(excluded))

        # 7. Report
        stats = {
            "candidates": len(candidates),
            "social_posts": len(social_posts),
            "reddit_posts": len(reddit_posts),
            "stocktwits_posts": len(stocktwits_posts),
            "news_articles": len(articles),
        }
        report = self.report_builder.build(
            ranked, run_date=run_date, excluded=excluded, stats=stats
        )

        # 8. Persist history
        if persist and self.store is not None:
            self.store.save_run(
                run_date, ranked,
                meta={"social_posts": len(social_posts),
                      "reddit_posts": len(reddit_posts),
                      "stocktwits_posts": len(stocktwits_posts),
                      "news_articles": len(articles),
                      "excluded": [c.ticker for c in excluded]},
            )

        # 9. Email
        message_id = None
        emailed = False
        if send_email and self.email is not None:
            message_id = self.email.send_report(report)
            emailed = True

        return RunResult(
            run_date=run_date,
            report=report,
            ranked_count=len(ranked),
            excluded_count=len(excluded),
            candidates=candidates,
            message_id=message_id,
            emailed=emailed,
        )


def store_region_client(config: Config) -> Any:
    """Build a region-pinned SES client (kept separate for easy patching)."""
    import boto3

    return boto3.client("ses", region_name=config.aws_region)
