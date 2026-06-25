"""Tests for run-history persistence + query helpers (moto-backed)."""
from stock_agent.models import Fundamentals, ScoredCandidate


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
