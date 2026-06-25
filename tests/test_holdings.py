"""Tests for the holdings (portfolio tracker) feature."""
from stock_agent.models import (
    Fundamentals,
    PriceFactors,
    ScoredCandidate,
    SentimentResult,
)
from stock_agent.report.builder import ReportBuilder, holding_signal


def _cand(ticker="XYZ", final=72.0, fund=75.0, sent=60.0, gated=False,
          mom=None, name="XYZ Inc.", with_fund=True, price=100.0):
    f = (Fundamentals(ticker=ticker, name=name, current_price=price,
                      sector="Technology", revenue_growth=0.1, roe=0.2)
         if with_fund else None)
    factors = (PriceFactors(ticker=ticker, momentum=mom, volatility=0.3,
                            beta=1.0, max_drawdown=-0.1, avg_dollar_volume=5e7)
               if mom is not None else None)
    return ScoredCandidate(
        ticker=ticker, final_score=final, fundamentals_score=fund,
        sentiment_score=sent, gated=gated, fundamentals=f,
        sentiment=SentimentResult(ticker=ticker, mention_count=5,
                                  avg_sentiment=0.3, news_count=2,
                                  avg_news_sentiment=0.2),
        factors=factors, rationale=f"{ticker} rationale",
    )


# ---------------- signal logic ----------------
def test_signal_add_when_strong_with_confirming_momentum():
    label, reason = holding_signal(_cand(final=82, sent=62, mom=0.30))
    assert label == "ADD"
    assert "momentum" in reason and "strong" in reason


def test_signal_trim_when_weak_score():
    label, reason = holding_signal(_cand(final=38, sent=40, mom=-0.05))
    assert label == "TRIM"
    assert "weak" in reason


def test_signal_trim_when_negative_momentum_and_soft_sentiment():
    label, _ = holding_signal(_cand(final=58, sent=40, mom=-0.25))
    assert label == "TRIM"


def test_signal_hold_when_stable_mid():
    label, _ = holding_signal(_cand(final=60, sent=50, mom=0.05))
    assert label == "HOLD"


def test_signal_strong_score_but_negative_momentum_is_not_add():
    # Deteriorating price action vetoes an ADD even with a strong score.
    label, _ = holding_signal(_cand(final=80, sent=62, mom=-0.30))
    assert label != "ADD"


def test_signal_watch_when_no_fundamentals():
    label, reason = holding_signal(_cand(with_fund=False))
    assert label == "WATCH"
    assert "data" in reason.lower()


def test_signal_watch_when_fundamentals_errored():
    f = Fundamentals(ticker="ERR", error="not found")
    c = ScoredCandidate(ticker="ERR", final_score=0, fundamentals_score=0,
                        sentiment_score=0, fundamentals=f)
    assert holding_signal(c)[0] == "WATCH"


# ---------------- report rendering ----------------
def test_holdings_section_text_and_html():
    holdings = [_cand("NVDA", final=85, sent=65, mom=0.45, name="Nvidia"),
                _cand("INTC", final=40, sent=38, mom=-0.30, name="Intel")]
    report = ReportBuilder(top_n=10).build(
        [], run_date="2026-06-25", holdings=holdings)

    t = report.text_body
    assert "YOUR HOLDINGS" in t
    assert "NVDA" in t and "INTC" in t
    assert "[ADD]" in t and "[TRIM]" in t
    assert "Signal:" in t and "Sentiment:" in t

    h = report.html_body
    assert "Your holdings" in h
    assert "NVDA" in h and "INTC" in h
    # Signal badges present.
    assert ">ADD<" in h and ">TRIM<" in h


def test_holdings_absent_when_none():
    report = ReportBuilder(top_n=10).build([], run_date="2026-06-25")
    assert "YOUR HOLDINGS" not in report.text_body
    assert "Your holdings" not in report.html_body


# ---------------- storage ----------------
def test_holdings_add_list_remove(store):
    assert store.list_holdings() == []
    store.add_holding("nvda")
    store.add_holding("AAPL", note="core position")
    assert store.list_holdings() == ["AAPL", "NVDA"]  # upper + sorted
    store.remove_holding("nvda")
    assert store.list_holdings() == ["AAPL"]


def test_holdings_and_watchlist_are_independent(store):
    store.add_to_watchlist("TSLA")
    store.add_holding("AAPL")
    assert store.list_watchlist() == ["TSLA"]
    assert store.list_holdings() == ["AAPL"]
