"""End-to-end orchestrator test with fully mocked collaborators (no net/AWS)."""
from stock_agent.config import Config
from stock_agent.orchestrator import Orchestrator
from stock_agent.extraction.ticker_extractor import TickerExtractor
from stock_agent.scoring.engine import ScoringEngine
from stock_agent.report.builder import ReportBuilder
from stock_agent.sentiment.base import SentimentAggregator
from stock_agent.models import Fundamentals, NewsArticle, RedditPost


class FakeReddit:
    def __init__(self, posts):
        self._posts = posts

    def collect(self, now=None):
        return self._posts


class FakeNews:
    def __init__(self, articles):
        self._articles = articles

    def collect(self, now=None):
        return self._articles


class FakeFetcher:
    def __init__(self, mapping):
        self._mapping = mapping

    def fetch_many(self, tickers):
        return {t: self._mapping.get(t, Fundamentals(ticker=t, error="not found"))
                for t in tickers}


class FakeStore:
    def __init__(self, watchlist=None):
        self._watchlist = watchlist or []
        self.saved = []

    def list_watchlist(self):
        return list(self._watchlist)

    def save_run(self, run_date, candidates, meta=None):
        self.saved.append((run_date, candidates, meta))


class FakeEmail:
    def __init__(self):
        self.sent = []

    def send_report(self, report):
        self.sent.append(report)
        return "msg-e2e"


class KeywordAnalyzer:
    def score(self, text):
        t = text.lower()
        if "great" in t or "strong" in t:
            return 0.8
        if "bankrupt" in t or "fraud" in t:
            return -0.8
        return 0.0


def _build(config=None):
    config = config or Config()
    posts = [
        RedditPost(id="1", subreddit="stocks", title="AAPL is great",
                   body="strong fundamentals", score=100, created_utc=0.0),
        RedditPost(id="2", subreddit="stocks", title="AAPL great again",
                   body="love it", score=50, created_utc=0.0),
        RedditPost(id="3", subreddit="stocks", title="MEME to the moon great",
                   body="great great great", score=999, created_utc=0.0),
    ]
    articles = [
        NewsArticle(title="AAPL posts strong quarter", summary="great results",
                    link="x", source="news"),
    ]
    funds = {
        "AAPL": Fundamentals(ticker="AAPL", revenue_growth=0.15,
                             earnings_growth=0.20, profit_margin=0.25, roe=0.30,
                             debt_to_equity=0.4, free_cash_flow=1e11,
                             trailing_pe=25.0, peg_ratio=1.4, current_price=190,
                             target_mean_price=210, name="Apple Inc."),
        "MEME": Fundamentals(ticker="MEME", revenue_growth=-0.30,
                             earnings_growth=-0.40, profit_margin=-0.15,
                             roe=-0.10, debt_to_equity=3.5, free_cash_flow=-1e9,
                             trailing_pe=-3.0, name="Meme Co"),
    }
    extractor = TickerExtractor()
    return Orchestrator(
        config=config,
        reddit_collector=FakeReddit(posts),
        news_collector=FakeNews(articles),
        fundamentals_fetcher=FakeFetcher(funds),
        store=FakeStore(),
        sentiment_aggregator=SentimentAggregator(KeywordAnalyzer(), extractor),
        extractor=extractor,
        scoring_engine=ScoringEngine(config),
        report_builder=ReportBuilder(top_n=config.top_n),
        email_sender=FakeEmail(),
    )


def test_end_to_end_run_produces_report_and_persists_and_emails():
    orch = _build()
    result = orch.run(run_date="2026-06-25")

    # AAPL (strong) should rank #1; MEME hype-gated below it.
    assert result.ranked_count >= 1
    assert result.candidates  # discovered something
    assert "AAPL" in result.report.text_body
    assert result.emailed is True
    assert result.message_id == "msg-e2e"

    # Persisted exactly one run with AAPL ranked first.
    saved_date, saved_cands, meta = orch.store.saved[0]
    assert saved_date == "2026-06-25"
    assert saved_cands[0].ticker == "AAPL"
    assert meta["reddit_posts"] == 3

    # Email payload is the same report.
    assert orch.email.sent[0].subject == result.report.subject


def test_hype_gate_keeps_meme_below_quality_name_end_to_end():
    orch = _build()
    result = orch.run(run_date="2026-06-25")
    text = result.report.text_body
    # AAPL appears before MEME in the ranked report (if MEME qualifies at all).
    if "MEME" in text:
        assert text.index("AAPL") < text.index("MEME")


def test_run_can_skip_email_and_persist():
    orch = _build()
    result = orch.run(run_date="2026-06-25", send_email=False, persist=False)
    assert result.emailed is False
    assert result.message_id is None
    assert orch.store.saved == []


def test_watchlist_ticker_included_even_without_mentions():
    config = Config()
    orch = _build(config)
    orch.store = FakeStore(watchlist=["TSLA"])
    # Give TSLA fundamentals so it can be scored.
    orch.fundamentals._mapping["TSLA"] = Fundamentals(
        ticker="TSLA", revenue_growth=0.10, earnings_growth=0.05,
        profit_margin=0.10, roe=0.12, debt_to_equity=0.5, trailing_pe=60.0,
        free_cash_flow=1e9, name="Tesla")
    result = orch.run(run_date="2026-06-25", send_email=False)
    assert "TSLA" in result.candidates


class FakeStockTwits:
    def __init__(self, posts):
        self._posts = posts
        self.watchlist_arg = None

    def collect(self, watchlist=None, now=None):
        self.watchlist_arg = watchlist
        return self._posts


def test_stocktwits_and_reddit_aggregate_as_social():
    from stock_agent.models import SocialPost

    config = Config()
    orch = _build(config)
    # Add a StockTwits collector that surfaces a NEW ticker (COIN) plus AAPL buzz.
    st_posts = [
        SocialPost(id="st-1", source="stocktwits", title="",
                   body="$AAPL strong momentum", score=3, created_utc=0.0,
                   native_sentiment="Bullish"),
        SocialPost(id="st-2", source="stocktwits", title="",
                   body="$COIN great breakout", score=9, created_utc=0.0,
                   native_sentiment="Bullish"),
    ]
    orch.stocktwits = FakeStockTwits(st_posts)
    orch.fundamentals._mapping["COIN"] = Fundamentals(
        ticker="COIN", revenue_growth=0.20, earnings_growth=0.30,
        profit_margin=0.20, roe=0.18, debt_to_equity=0.5, trailing_pe=25.0,
        free_cash_flow=1e9, name="Coinbase")

    result = orch.run(run_date="2026-06-25", send_email=False, persist=False)

    # COIN discovered via StockTwits; watchlist forwarded to the collector.
    assert "COIN" in result.candidates
    assert orch.stocktwits.watchlist_arg is not None
    # Report reflects combined social posts (reddit + stocktwits).
    assert "stocktwits" in result.report.text_body.lower()


class FakeNewsProvider:
    def __init__(self, mapping):
        self._mapping = mapping

    def recent(self, ticker, limit=3):
        return list(self._mapping.get(ticker, []))[:limit]


def test_rss_news_attached_to_picks():
    # The default fixture's article "AAPL posts strong quarter" names AAPL,
    # so RSS-derived news should attach to the AAPL pick automatically.
    orch = _build()
    result = orch.run(run_date="2026-06-25", send_email=False, persist=False)
    assert "Recent news" in result.report.text_body
    assert "AAPL posts strong quarter" in result.report.text_body


def test_news_provider_headlines_attached_and_deduped():
    from stock_agent.models import NewsRef

    orch = _build()
    orch.news_provider = FakeNewsProvider({
        "AAPL": [
            # Duplicate of the RSS headline (same title) must be de-duped.
            NewsRef(title="AAPL posts strong quarter", source="Yahoo"),
            NewsRef(title="Apple ships record iPhones", source="CNBC",
                    url="https://example.com/iphone"),
        ]
    })
    result = orch.run(run_date="2026-06-25", send_email=False, persist=False)
    text = result.report.text_body
    assert "Apple ships record iPhones" in text
    # De-dup: the shared headline appears once.
    assert text.count("AAPL posts strong quarter") == 1


def test_news_provider_failure_does_not_break_run():
    class Boom:
        def recent(self, ticker, limit=3):
            raise RuntimeError("news API down")

    orch = _build()
    orch.news_provider = Boom()
    result = orch.run(run_date="2026-06-25", send_email=False, persist=False)
    # Run still completes and produces a report.
    assert result.ranked_count >= 1
    assert "AAPL" in result.report.text_body


# --- #5: fixed universe decoupled from social discovery ---
def test_fixed_universe_includes_unmentioned_index_names():
    from stock_agent.universe import FixedUniverse

    config = Config(max_candidates=6)
    orch = _build(config)
    orch.universe = FixedUniverse(["AAPL", "MEME", "NVDA", "MSFT", "KO", "JNJ"])
    # NVDA is never mentioned in any post, yet it's in the index.
    orch.fundamentals._mapping["NVDA"] = Fundamentals(
        ticker="NVDA", revenue_growth=0.50, earnings_growth=0.50,
        profit_margin=0.30, roe=0.40, debt_to_equity=0.3,
        free_cash_flow=1e10, trailing_pe=30.0, sector="Tech", name="Nvidia")
    result = orch.run(run_date="2026-06-25", send_email=False, persist=False)
    # Screened purely because it's an index member, not because of social buzz.
    assert "NVDA" in result.candidates
    assert "AAPL" in result.candidates


def test_fixed_universe_ignores_offindex_hype_but_keeps_watchlist():
    from stock_agent.universe import FixedUniverse

    config = Config(max_candidates=6)
    orch = _build(config)
    orch.store = FakeStore(watchlist=["TSLA"])  # off-index escape hatch
    orch.universe = FixedUniverse(["AAPL", "NVDA", "MSFT", "KO", "JNJ", "XOM"])
    orch.fundamentals._mapping["TSLA"] = Fundamentals(
        ticker="TSLA", revenue_growth=0.10, earnings_growth=0.05,
        profit_margin=0.10, roe=0.12, debt_to_equity=0.5, trailing_pe=60.0,
        free_cash_flow=1e9, name="Tesla")
    result = orch.run(run_date="2026-06-25", send_email=False, persist=False)
    # MEME is mentioned socially but is NOT in the index -> dropped.
    assert "MEME" not in result.candidates
    # Watchlist name is included even though off-index.
    assert "TSLA" in result.candidates


# --- #4: price factors threaded through scoring + persistence ---
class FakeFactorFetcher:
    def __init__(self, factors):
        self._factors = factors

    def fetch_many(self, tickers):
        from stock_agent.models import PriceFactors
        return {t: self._factors.get(t, PriceFactors(ticker=t, error="none"))
                for t in tickers}


def test_factors_tilt_score_surface_in_report_and_persist():
    from stock_agent.models import PriceFactors

    orch = _build()
    orch.factor_fetcher = FakeFactorFetcher({
        "AAPL": PriceFactors(ticker="AAPL", momentum=0.40, volatility=0.22,
                             beta=1.1, max_drawdown=-0.12,
                             avg_dollar_volume=8e8),
    })
    store = FakeStore()
    orch.store = store
    result = orch.run(run_date="2026-06-25", send_email=False, persist=True)

    # Risk/momentum line surfaced in the report.
    assert "Risk" in result.report.text_body
    assert "12-mo" in result.report.text_body or "vol" in result.report.text_body

    saved_cands = store.saved[0][1]
    top = saved_cands[0]
    assert top.ticker == "AAPL"
    assert top.factors is not None and top.factors.momentum == 0.40
    assert any("momentum" in s.lower() for s in top.supporting_signals)


def test_factor_fetch_failure_does_not_break_run():
    class Boom:
        def fetch_many(self, tickers):
            raise RuntimeError("yfinance down")

    orch = _build()
    orch.factor_fetcher = Boom()
    result = orch.run(run_date="2026-06-25", send_email=False, persist=False)
    assert result.ranked_count >= 1
    assert "AAPL" in result.report.text_body
