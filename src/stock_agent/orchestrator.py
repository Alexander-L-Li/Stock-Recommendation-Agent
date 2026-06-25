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
from .models import NewsRef
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
        news_provider: Any = None,
        factor_fetcher: Any = None,
        universe_provider: Any = None,
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
        self.news_provider = news_provider
        self.factor_fetcher = factor_fetcher
        self.universe = universe_provider

    @classmethod
    def build_default(cls, config: Config, store: Any = None) -> "Orchestrator":
        """Construct the production wiring with real collaborators."""
        from .collectors.news_collector import NewsCollector
        from .collectors.reddit_collector import RedditCollector
        from .collectors.stocktwits_collector import StockTwitsCollector
        from .collectors.ticker_news import YFinanceNewsProvider
        from .delivery.ses_sender import SesEmailSender
        from .fundamentals.price_factors import PriceFactorFetcher
        from .fundamentals.yfinance_fetcher import FundamentalsFetcher
        from .sentiment.vader import VaderAnalyzer
        from .storage.dynamo import Store
        from .universe import FixedUniverse

        store = store or Store(config.table_name, region=config.aws_region)
        extractor = TickerExtractor()
        reddit = RedditCollector(config) if config.enable_reddit else None
        stocktwits = (StockTwitsCollector(config)
                      if config.enable_stocktwits else None)
        factor_fetcher = (
            PriceFactorFetcher(benchmark=config.price_benchmark)
            if config.enable_price_factors else None
        )
        universe = FixedUniverse() if config.enable_fixed_universe else None
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
            news_provider=YFinanceNewsProvider(),
            factor_fetcher=factor_fetcher,
            universe_provider=universe,
        )

    def run(self, run_date: Optional[str] = None, send_email: bool = True,
            persist: bool = True) -> RunResult:
        run_date = run_date or date.today().isoformat()
        logger.info("Starting run for %s", run_date)

        # 1. Watchlist + holdings (both always analyzed; holdings also get a
        #    dedicated report section). They drive discovery and StockTwits
        #    symbol selection too.
        watchlist = self.store.list_watchlist() if self.store else []
        holdings = []
        if (self.store is not None and self.config.enable_holdings
                and hasattr(self.store, "list_holdings")):
            holdings = self.store.list_holdings()
        # Names that must always be analyzed regardless of social/universe.
        always_include = list(dict.fromkeys(list(watchlist) + list(holdings)))

        # 2. Collect social sources (Reddit + StockTwits) and news.
        reddit_posts = self.reddit.collect() if self.reddit is not None else []
        stocktwits_posts = (
            self.stocktwits.collect(watchlist=always_include)
            if self.stocktwits is not None else []
        )
        social_posts = list(reddit_posts) + list(stocktwits_posts)
        articles = self.news.collect()
        logger.info("Collected %d reddit + %d stocktwits social posts, "
                    "%d news articles", len(reddit_posts),
                    len(stocktwits_posts), len(articles))

        # 3. Candidate universe.
        #    #5: when a fixed universe is wired, the analyzed set is the index
        #    (watchlist always included); social mention counts become a
        #    prioritization overlay rather than the gatekeeper. Otherwise fall
        #    back to pure social/news discovery.
        if self.universe is not None:
            texts = ([p.text for p in social_posts]
                     + [a.text for a in articles])
            mention_counts = self.extractor.count_mentions(texts)
            candidates = self.universe.select(
                mention_counts=mention_counts,
                watchlist=always_include,
                max_candidates=self.config.max_candidates,
                run_date=run_date,
            )
            logger.info("Universe: %d candidates from fixed index of %d "
                        "(%d socially-mentioned, %d always-include)",
                        len(candidates), len(self.universe),
                        sum(1 for t in candidates if mention_counts.get(t, 0) > 0),
                        len(always_include))
        else:
            candidates, _counts = self.extractor.discover(
                social_posts, articles, watchlist=always_include,
                min_mentions=self.config.min_mentions,
                max_candidates=self.config.max_candidates,
            )
            logger.info("Discovered %d candidates (%d always-include)",
                        len(candidates), len(always_include))

        # 4. Sentiment (Reddit + StockTwits aggregate as one social signal)
        sentiment = {}
        if self.sentiment is not None:
            sentiment = self.sentiment.aggregate(social_posts, articles, candidates)

        # 5. Fundamentals
        fundamentals = self.fundamentals.fetch_many(candidates)

        # 5b. Price-based risk/momentum factors (#4), best-effort.
        factors = {}
        if self.factor_fetcher is not None:
            try:
                factors = self.factor_fetcher.fetch_many(candidates)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Price factor fetch failed: %s", exc)

        # 6. Score + rank (sector-relative #3 + risk tilt #4 applied here)
        ranked, excluded = self.engine.rank(fundamentals, sentiment, factors)
        logger.info("Ranked %d, excluded %d", len(ranked), len(excluded))

        # 6b. Build the holdings view (always shown, regardless of rank) and
        #     attach recent stock-specific news to every card that will be
        #     rendered (top picks + holdings).
        by_ticker = {c.ticker: c for c in list(ranked) + list(excluded)}
        holdings_cands = [by_ticker[t] for t in holdings if t in by_ticker]
        top_picks = ranked[: self.config.top_n]
        shown = list(top_picks)
        shown_tickers = {c.ticker for c in top_picks}
        for c in holdings_cands:
            if c.ticker not in shown_tickers:
                shown.append(c)
                shown_tickers.add(c.ticker)
        self._attach_news(shown, articles)

        # 7. Report
        stats = {
            "candidates": len(candidates),
            "social_posts": len(social_posts),
            "reddit_posts": len(reddit_posts),
            "stocktwits_posts": len(stocktwits_posts),
            "news_articles": len(articles),
        }
        report = self.report_builder.build(
            ranked, run_date=run_date, excluded=excluded, stats=stats,
            holdings=holdings_cands,
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

    def _attach_news(self, picks: list, articles: list) -> None:
        """Attach up to 3 recent, stock-specific headlines to each pick.

        Two sources, merged and de-duplicated by title:
          1. RSS articles already collected this run that name the ticker
             (free, deterministic).
          2. Best-effort per-ticker headlines from ``news_provider`` (yfinance),
             which is where most stock-specific coverage comes from. Any failure
             there is swallowed so it never breaks the run.
        """
        rss_by_ticker: dict[str, list[NewsRef]] = {}
        for a in articles:
            for t in self.extractor.extract_from_text(a.text):
                rss_by_ticker.setdefault(t, []).append(
                    NewsRef(title=a.title, source=a.source, url=a.link,
                            published=a.published)
                )

        for c in picks:
            refs: list[NewsRef] = list(rss_by_ticker.get(c.ticker, []))
            if self.news_provider is not None:
                try:
                    refs.extend(self.news_provider.recent(c.ticker, limit=3))
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("News provider failed for %s: %s",
                                   c.ticker, exc)
            # De-dupe by normalized title, keep most recent first.
            seen: set[str] = set()
            unique: list[NewsRef] = []
            for r in sorted(refs, key=lambda x: x.sort_key(), reverse=True):
                key = r.title.strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                unique.append(r)
            c.news = unique[:3]


def store_region_client(config: Config) -> Any:
    """Build a region-pinned SES client (kept separate for easy patching)."""
    import boto3

    return boto3.client("ses", region_name=config.aws_region)
