from stock_agent.sentiment.base import SentimentAggregator
from stock_agent.sentiment.vader import VaderAnalyzer
from stock_agent.extraction.ticker_extractor import TickerExtractor
from stock_agent.models import NewsArticle, RedditPost


def _post(title, body=""):
    return RedditPost(id="x", subreddit="stocks", title=title, body=body,
                      score=1, created_utc=0.0)


def _article(title, summary=""):
    return NewsArticle(title=title, summary=summary, link="", source="t")


# --- VADER backend ---
def test_vader_polarity_directions():
    v = VaderAnalyzer()
    pos = v.score("This is an amazing, fantastic, wonderful company!")
    neg = v.score("This is a terrible, awful, horrible disaster.")
    neutral = v.score("The company filed a report.")
    assert pos > 0.3
    assert neg < -0.3
    assert -0.3 <= neutral <= 0.3


def test_vader_empty_is_zero():
    assert VaderAnalyzer().score("") == 0.0


# --- A deterministic fake analyzer for aggregation tests ---
class KeywordAnalyzer:
    """Returns +1 if 'good' in text, -1 if 'bad', else 0."""

    def score(self, text):
        t = text.lower()
        if "good" in t:
            return 1.0
        if "bad" in t:
            return -1.0
        return 0.0


def test_aggregate_counts_and_average():
    agg = SentimentAggregator(KeywordAnalyzer(), TickerExtractor())
    posts = [
        _post("AAPL is good"),       # +1
        _post("AAPL is bad"),        # -1
        _post("NVDA is good"),       # +1
    ]
    results = agg.aggregate(posts, [])
    assert results["AAPL"].mention_count == 2
    assert abs(results["AAPL"].avg_sentiment - 0.0) < 1e-9  # (1 + -1)/2
    assert results["NVDA"].mention_count == 1
    assert results["NVDA"].avg_sentiment == 1.0


def test_aggregate_news_separately():
    agg = SentimentAggregator(KeywordAnalyzer(), TickerExtractor())
    articles = [_article("AAPL posts good results"), _article("AAPL good again")]
    results = agg.aggregate([], articles)
    assert results["AAPL"].news_count == 2
    assert results["AAPL"].avg_news_sentiment == 1.0
    assert results["AAPL"].mention_count == 0


def test_aggregate_respects_candidate_allowlist():
    agg = SentimentAggregator(KeywordAnalyzer(), TickerExtractor())
    posts = [_post("AAPL is good"), _post("NVDA is good")]
    results = agg.aggregate(posts, [], candidates=["AAPL"])
    assert "AAPL" in results
    assert "NVDA" not in results


def test_pluggable_interface_swap():
    # The aggregator works with any object exposing score(text)->float.
    class AlwaysPositive:
        def score(self, text):
            return 0.5

    agg = SentimentAggregator(AlwaysPositive(), TickerExtractor())
    results = agg.aggregate([_post("AAPL update")], [])
    assert results["AAPL"].avg_sentiment == 0.5


# --- StockTwits native sentiment blending ---
from stock_agent.models import SocialPost


def _social(body, native=None):
    return SocialPost(id="s1", source="stocktwits", title="", body=body,
                      score=0, created_utc=0.0, native_sentiment=native)


def test_native_bullish_lifts_sentiment():
    # Neutral text, but tagged Bullish -> positive blended score.
    agg = SentimentAggregator(KeywordAnalyzer(), TickerExtractor())
    res = agg.aggregate([_social("$AAPL no opinion", native="Bullish")], [])
    assert res["AAPL"].avg_sentiment > 0.1


def test_native_bearish_lowers_sentiment():
    agg = SentimentAggregator(KeywordAnalyzer(), TickerExtractor())
    res = agg.aggregate([_social("$AAPL no opinion", native="Bearish")], [])
    assert res["AAPL"].avg_sentiment < -0.1


def test_social_posts_aggregate_with_reddit_uniformly():
    # A RedditPost and a SocialPost for the same ticker both count as social.
    agg = SentimentAggregator(KeywordAnalyzer(), TickerExtractor())
    reddit = _post("AAPL is good")          # KeywordAnalyzer -> +1
    st = _social("$AAPL is good", native=None)  # +1
    res = agg.aggregate([reddit, st], [])
    assert res["AAPL"].mention_count == 2
    assert res["AAPL"].avg_sentiment == 1.0
