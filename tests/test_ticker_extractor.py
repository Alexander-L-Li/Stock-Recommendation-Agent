from collections import Counter

from stock_agent.extraction.ticker_extractor import TickerExtractor
from stock_agent.models import NewsArticle, RedditPost


def _post(title, body=""):
    return RedditPost(id="x", subreddit="stocks", title=title, body=body,
                      score=1, created_utc=0.0)


def _article(title, summary=""):
    return NewsArticle(title=title, summary=summary, link="", source="t")


def test_cashtags_always_extracted():
    ex = TickerExtractor()
    found = ex.extract_from_text("I'm long $AAPL and $nvda here")
    assert found == {"AAPL", "NVDA"}


def test_allcaps_validated_against_known_list():
    ex = TickerExtractor()
    # AAPL is known -> kept; ZZZZ not known -> dropped
    found = ex.extract_from_text("AAPL is great but ZZZZ is unknown")
    assert "AAPL" in found
    assert "ZZZZ" not in found


def test_false_positive_acronyms_dropped():
    ex = TickerExtractor()
    text = "The CEO told the SEC and FDA that USA GDP and AI are key. Buy NVDA."
    found = ex.extract_from_text(text)
    assert found == {"NVDA"}
    for noise in ("CEO", "SEC", "FDA", "USA", "GDP", "AI"):
        assert noise not in found


def test_cashtag_overrides_stopword_safety():
    # A cashtag for a real token still excluded if it's a stopword (e.g. $AI)
    ex = TickerExtractor()
    found = ex.extract_from_text("$AI hype vs $PLTR")
    assert "PLTR" in found
    assert "AI" not in found  # AI is in stopwords


def test_count_mentions_one_per_text():
    ex = TickerExtractor()
    texts = ["AAPL AAPL AAPL good", "AAPL again", "NVDA only"]
    counts = ex.count_mentions(texts)
    assert counts["AAPL"] == 2  # two distinct texts
    assert counts["NVDA"] == 1


def test_discover_merges_watchlist_and_orders_by_mentions():
    ex = TickerExtractor()
    posts = [_post("AAPL strong"), _post("AAPL again"), _post("NVDA up")]
    articles = [_article("AAPL earnings")]
    candidates, counter = ex.discover(
        posts, articles, watchlist=["TSLA"], min_mentions=1
    )
    # AAPL mentioned 3x, NVDA 1x, TSLA from watchlist (0 mentions) last
    assert candidates[0] == "AAPL"
    assert "NVDA" in candidates
    assert "TSLA" in candidates
    assert counter["AAPL"] == 3


def test_discover_min_mentions_filter():
    ex = TickerExtractor()
    posts = [_post("AAPL strong"), _post("NVDA up")]
    candidates, _ = ex.discover(posts, [], min_mentions=2)
    # Neither reaches 2 mentions -> empty discovery
    assert candidates == []


def test_discover_caps_but_keeps_watchlist():
    ex = TickerExtractor()
    posts = [
        _post("AAPL a"), _post("AAPL b"), _post("AAPL c"),
        _post("NVDA a"), _post("NVDA b"),
        _post("MSFT a"),
    ]
    candidates, _ = ex.discover(
        posts, [], watchlist=["TSLA"], min_mentions=1, max_candidates=2
    )
    # Top 2 by mentions are AAPL, NVDA; TSLA appended despite cap
    assert "AAPL" in candidates and "NVDA" in candidates
    assert "TSLA" in candidates
    assert "MSFT" not in candidates
