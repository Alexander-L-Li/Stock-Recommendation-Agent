"""Tests for the StockTwits collector using an injected fake HTTP getter."""
import time

from stock_agent.config import Config
from stock_agent.collectors.stocktwits_collector import StockTwitsCollector


def _msg(mid, body, when_epoch, sentiment=None, likes=0):
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when_epoch))
    m = {"id": mid, "body": body, "created_at": created,
         "likes": {"total": likes}}
    if sentiment:
        m["entities"] = {"sentiment": {"basic": sentiment}}
    return m


def _fake_http(trending_symbols, streams):
    def get(url):
        if "trending/symbols" in url:
            return {"symbols": [{"symbol": s} for s in trending_symbols]}
        for sym, msgs in streams.items():
            if f"/symbol/{sym}.json" in url:
                return {"messages": msgs}
        return {"messages": []}
    return get


def _cfg(**kw):
    base = dict(lookback_hours=24, stocktwits_symbol_limit=20)
    base.update(kw)
    return Config(**base)


def test_collect_trending_symbol_messages():
    now = 1_000_000.0
    recent = now - 3600
    http = _fake_http(
        ["AAPL"],
        {"AAPL": [_msg(1, "$AAPL looks strong", recent, "Bullish", likes=4)]},
    )
    posts = StockTwitsCollector(_cfg(), http_get=http).collect(now=now)
    assert len(posts) == 1
    p = posts[0]
    assert p.source == "stocktwits"
    assert p.kind == "stocktwits"
    assert "$AAPL" in p.text
    assert p.score == 4
    assert p.native_sentiment == "Bullish"
    assert p.id == "st-1"


def test_cashtag_prepended_when_missing():
    now = 1_000_000.0
    http = _fake_http(["NVDA"], {"NVDA": [_msg(2, "great quarter no tag", now - 60)]})
    posts = StockTwitsCollector(_cfg(), http_get=http).collect(now=now)
    assert posts[0].text.startswith("$NVDA")


def test_old_messages_filtered():
    now = 1_000_000.0
    http = _fake_http(["AAPL"], {"AAPL": [
        _msg(1, "$AAPL fresh", now - 3600),
        _msg(2, "$AAPL stale", now - 48 * 3600),
    ]})
    posts = StockTwitsCollector(_cfg(), http_get=http).collect(now=now)
    ids = {p.id for p in posts}
    assert ids == {"st-1"}


def test_watchlist_symbols_included_and_capped():
    now = 1_000_000.0
    recent = now - 60
    streams = {s: [_msg(i, f"${s} hi", recent)] for i, s in
               enumerate(["AAA", "BBB", "CCC", "WMT"])}
    http = _fake_http(["AAA", "BBB", "CCC"], streams)
    cfg = _cfg(stocktwits_symbol_limit=2)
    posts = StockTwitsCollector(cfg, http_get=http).collect(
        watchlist=["WMT"], now=now)
    # cap=2 over (trending ∪ watchlist) ordered trending-first
    symbols_seen = {p.url.rsplit("/", 1)[-1] for p in posts}
    assert len(symbols_seen) <= 2


def test_trending_failure_falls_back_to_watchlist():
    now = 1_000_000.0
    recent = now - 60

    def get(url):
        if "trending" in url:
            raise RuntimeError("503")
        return {"messages": [_msg(9, "$TSLA up", recent)]}

    posts = StockTwitsCollector(_cfg(), http_get=get).collect(
        watchlist=["TSLA"], now=now)
    assert any("$TSLA" in p.text for p in posts)


def test_bad_symbol_does_not_abort():
    now = 1_000_000.0
    recent = now - 60

    def get(url):
        if "trending" in url:
            return {"symbols": [{"symbol": "BAD"}, {"symbol": "AAPL"}]}
        if "/symbol/BAD.json" in url:
            raise RuntimeError("404")
        return {"messages": [_msg(1, "$AAPL ok", recent)]}

    posts = StockTwitsCollector(_cfg(), http_get=get).collect(now=now)
    assert any("$AAPL" in p.text for p in posts)


def test_crypto_dotx_symbols_filtered():
    now = 1_000_000.0
    recent = now - 60
    http = _fake_http(["BTC.X", "AAPL"], {
        "AAPL": [_msg(1, "$AAPL ok", recent)],
        "BTC.X": [_msg(2, "$BTC.X moon", recent)],
    })
    posts = StockTwitsCollector(_cfg(), http_get=http).collect(now=now)
    assert all("BTC.X" not in p.text for p in posts)
    assert any("$AAPL" in p.text for p in posts)
