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


# ---------------- holdings excluded from the top picks ----------------
def _picks_region(text: str) -> str:
    """The slice of the text report at/after the top-picks header."""
    marker = "TOP PICKS"
    idx = text.find(marker)
    return text[idx:] if idx >= 0 else text


def test_held_ticker_is_excluded_from_top_picks_text():
    held = _cand("NVDA", final=95, name="Nvidia")          # would rank #1
    a = _cand("AAA", final=80, name="Alpha")
    b = _cand("BBB", final=70, name="Bravo")
    report = ReportBuilder(top_n=10).build(
        [held, a, b], run_date="2026-06-25", holdings=[held])

    picks = _picks_region(report.text_body)
    assert "NVDA" not in picks          # held -> not in the top picks list
    assert "AAA" in picks and "BBB" in picks
    # But it still appears in the holdings section above.
    assert "YOUR HOLDINGS" in report.text_body
    assert "NVDA" in report.text_body


def test_held_ticker_excluded_from_top_picks_html():
    held = _cand("NVDA", final=95, name="Nvidia")
    a = _cand("AAA", final=80, name="Alpha")
    report = ReportBuilder(top_n=10).build(
        [held, a], run_date="2026-06-25", holdings=[held])
    h = report.html_body
    picks_html = h[h.find("Today's top picks"):]
    assert "AAA" in picks_html
    assert "NVDA" not in picks_html     # excluded from picks
    assert "Your holdings" in h         # but present as a holding


def test_top_picks_are_renumbered_without_gaps_when_holding_excluded():
    held = _cand("NVDA", final=95)
    a = _cand("AAA", final=80)
    b = _cand("BBB", final=70)
    report = ReportBuilder(top_n=10).build(
        [held, a, b], run_date="2026-06-25", holdings=[held])
    picks = _picks_region(report.text_body)
    # AAA should be displayed as #1 (no gap from the excluded #1 holding).
    assert "#1  AAA" in picks
    assert "#2  BBB" in picks

