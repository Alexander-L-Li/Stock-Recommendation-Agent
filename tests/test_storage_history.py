"""Tests for run-history persistence + query helpers (moto-backed)."""
from stock_agent.models import Fundamentals, ScoredCandidate, SentimentResult


def _cand(ticker, rank, final, fund=70.0, sent=60.0, gated=False):
    return ScoredCandidate(
        ticker=ticker, final_score=final, fundamentals_score=fund,
        sentiment_score=sent, gated=gated,
        fundamentals=Fundamentals(ticker=ticker, name=f"{ticker} Inc."),
        rationale=f"{ticker} looks good",
        supporting_signals=[f"{ticker} signal"],
        risks=[f"{ticker} risk"],
        rank=rank,
    )


def test_save_and_get_run(store):
    cands = [_cand("AAPL", 1, 88.0), _cand("MSFT", 2, 81.0, gated=False)]
    store.save_run("2026-06-25", cands,
                   meta={"reddit_posts": 50, "news_articles": 10})

    run = store.get_run("2026-06-25")
    assert run["meta"]["pick_count"] == 2
    assert run["meta"]["reddit_posts"] == 50
    assert run["meta"]["tickers"] == ["AAPL", "MSFT"]

    picks = run["picks"]
    assert [p["ticker"] for p in picks] == ["AAPL", "MSFT"]
    assert picks[0]["rank"] == 1
    assert picks[0]["final_score"] == 88.0
    assert picks[0]["rationale"] == "AAPL looks good"
    assert picks[0]["supporting_signals"] == ["AAPL signal"]


def test_get_run_missing_returns_empty(store):
    run = store.get_run("1999-01-01")
    assert run["meta"] == {}
    assert run["picks"] == []


def test_ticker_history_across_runs(store):
    store.save_run("2026-06-23", [_cand("AAPL", 1, 80.0)])
    store.save_run("2026-06-24", [_cand("AAPL", 2, 82.0)])
    store.save_run("2026-06-25", [_cand("AAPL", 1, 85.0)])

    history = store.get_ticker_history("aapl")
    # Most recent first
    assert [h["run_date"] for h in history] == \
        ["2026-06-25", "2026-06-24", "2026-06-23"]
    assert [h["final_score"] for h in history] == [85.0, 82.0, 80.0]
    assert [h["rank"] for h in history] == [1, 2, 1]


def test_ticker_history_limit(store):
    for day in range(1, 6):
        store.save_run(f"2026-06-0{day}", [_cand("NVDA", 1, 70.0 + day)])
    history = store.get_ticker_history("NVDA", limit=2)
    assert len(history) == 2
    assert history[0]["run_date"] == "2026-06-05"


def test_decimal_round_trip_preserves_floats(store):
    store.save_run("2026-06-25", [_cand("AAPL", 1, 88.55, fund=70.25, sent=61.5)])
    picks = store.get_run("2026-06-25")["picks"]
    assert picks[0]["final_score"] == 88.55
    assert picks[0]["fundamentals_score"] == 70.25


def test_runs_are_isolated_by_date(store):
    store.save_run("2026-06-24", [_cand("AAPL", 1, 80.0)])
    store.save_run("2026-06-25", [_cand("MSFT", 1, 90.0)])
    assert [p["ticker"] for p in store.get_run("2026-06-24")["picks"]] == ["AAPL"]
    assert [p["ticker"] for p in store.get_run("2026-06-25")["picks"]] == ["MSFT"]


def _cand_with_snapshot(ticker, rank, final):
    return ScoredCandidate(
        ticker=ticker, final_score=final, fundamentals_score=70.0,
        sentiment_score=60.0,
        fundamentals=Fundamentals(
            ticker=ticker, name=f"{ticker} Inc.", revenue_growth=0.15,
            roe=0.22, current_price=190.5, market_cap=3.0e12, sector="Technology",
        ),
        sentiment=SentimentResult(ticker=ticker, mention_count=8,
                                  avg_sentiment=0.4, news_count=2,
                                  avg_news_sentiment=0.3),
        rank=rank,
    )


def test_save_run_persists_point_in_time_snapshot(store):
    store.save_run("2026-06-25", [_cand_with_snapshot("AAPL", 1, 88.0)])
    pick = store.get_run("2026-06-25")["picks"][0]
    # entry_price + descriptive fields recorded for backtesting.
    assert pick["entry_price"] == 190.5
    assert pick["sector"] == "Technology"
    assert pick["market_cap"] == 3.0e12
    # Raw feature vector captured (subset checks).
    snap = pick["snapshot"]
    assert snap["fundamentals"]["revenue_growth"] == 0.15
    assert snap["fundamentals"]["roe"] == 0.22
    assert snap["fundamentals"]["current_price"] == 190.5
    assert snap["sentiment"]["mention_count"] == 8
    assert snap["sentiment"]["avg_sentiment"] == 0.4


def test_save_run_omits_entry_price_when_price_missing(store):
    # _cand() builds Fundamentals without current_price.
    store.save_run("2026-06-25", [_cand("NOPX", 1, 70.0)])
    pick = store.get_run("2026-06-25")["picks"][0]
    assert "entry_price" not in pick


def test_list_run_dates_most_recent_first(store):
    store.save_run("2026-06-23", [_cand("AAPL", 1, 80.0)])
    store.save_run("2026-06-25", [_cand("MSFT", 1, 90.0)])
    store.save_run("2026-06-24", [_cand("NVDA", 1, 85.0)])
    assert store.list_run_dates() == ["2026-06-25", "2026-06-24", "2026-06-23"]


def test_list_run_dates_empty_when_no_runs(store):
    assert store.list_run_dates() == []


def test_save_run_drops_non_finite_snapshot_values(store):
    # yfinance can emit inf/NaN for some ratios; DynamoDB rejects them, so the
    # store must drop them rather than crash the whole run.
    c = ScoredCandidate(
        ticker="INF", final_score=50.0, fundamentals_score=50.0,
        sentiment_score=50.0,
        fundamentals=Fundamentals(ticker="INF", trailing_pe=float("inf"),
                                  peg_ratio=float("nan"), roe=0.2,
                                  current_price=10.0),
        rank=1,
    )
    store.save_run("2026-06-25", [c])  # must not raise
    snap = store.get_run("2026-06-25")["picks"][0]["snapshot"]
    assert snap["fundamentals"]["roe"] == 0.2
    # inf/NaN dropped to None on serialize.
    assert snap["fundamentals"].get("trailing_pe") is None
    assert snap["fundamentals"].get("peg_ratio") is None
