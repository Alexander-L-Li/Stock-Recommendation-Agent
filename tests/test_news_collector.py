"""Tests for the news RSS collector using inline feed fixtures (no network)."""
import time
from email.utils import formatdate

import feedparser

from stock_agent.config import Config
from stock_agent.collectors.news_collector import NewsCollector


def _rss(items_xml: str, title: str = "Test Feed") -> str:
    return f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>{title}</title>
{items_xml}
</channel></rss>"""


def _item(title, desc, link, when_epoch=None):
    pubdate = ""
    if when_epoch is not None:
        pubdate = f"<pubDate>{formatdate(when_epoch)}</pubDate>"
    return (f"<item><title>{title}</title><description>{desc}</description>"
            f"<link>{link}</link>{pubdate}</item>")


def _config():
    return Config(news_feeds=["feed://fixture"], lookback_hours=24)


def _string_parser(_url):
    # Closure set per-test below via functools-like swap; placeholder.
    raise NotImplementedError


def test_parses_articles_from_feed():
    now = time.time()
    recent = now - 3600
    feed = _rss(_item("AAPL earnings beat", "Apple posted strong results",
                      "https://news/1", recent))
    collector = NewsCollector(_config(), parser=lambda url: feedparser.parse(feed))

    articles = collector.collect(now=now)

    assert len(articles) == 1
    a = articles[0]
    assert a.title == "AAPL earnings beat"
    assert "strong results" in a.summary
    assert a.link == "https://news/1"
    assert a.source == "Test Feed"
    assert a.published is not None


def test_filters_old_articles_but_keeps_undated():
    now = time.time()
    feed = _rss(
        _item("old", "stale", "https://n/old", now - 48 * 3600)
        + _item("new", "fresh", "https://n/new", now - 3600)
        + _item("undated", "no date", "https://n/undated", None)
    )
    collector = NewsCollector(_config(), parser=lambda url: feedparser.parse(feed))

    titles = {a.title for a in collector.collect(now=now)}
    assert "new" in titles
    assert "undated" in titles
    assert "old" not in titles


def test_empty_feed_returns_nothing():
    collector = NewsCollector(_config(), parser=lambda url: feedparser.parse(_rss("")))
    assert collector.collect(now=time.time()) == []


def test_malformed_feed_is_skipped():
    def boom(url):
        raise ValueError("garbage bytes")

    collector = NewsCollector(_config(), parser=boom)
    # Should not raise; just yields no articles.
    assert collector.collect(now=time.time()) == []


def test_multiple_feeds_aggregate():
    now = time.time()
    recent = now - 100
    feed_a = _rss(_item("A1", "x", "https://a/1", recent), title="FeedA")
    feed_b = _rss(_item("B1", "y", "https://b/1", recent), title="FeedB")
    feeds = {"a": feed_a, "b": feed_b}

    cfg = Config(news_feeds=["a", "b"], lookback_hours=24)
    collector = NewsCollector(cfg, parser=lambda url: feedparser.parse(feeds[url]))

    sources = {a.source for a in collector.collect(now=now)}
    assert sources == {"FeedA", "FeedB"}
