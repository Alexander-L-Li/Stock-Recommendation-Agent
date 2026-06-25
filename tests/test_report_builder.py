from stock_agent.report.builder import ReportBuilder
from stock_agent.scoring.engine import ScoringEngine
from stock_agent.models import (
    Fundamentals,
    NewsRef,
    ScoredCandidate,
    SentimentResult,
)


def _scored():
    eng = ScoringEngine()
    strong = eng.score_candidate(
        "AAPL",
        Fundamentals(ticker="AAPL", revenue_growth=0.15, earnings_growth=0.20,
                     profit_margin=0.25, roe=0.30, debt_to_equity=0.4,
                     free_cash_flow=1e11, trailing_pe=28.0, peg_ratio=1.5,
                     current_price=190.0, target_mean_price=210.0,
                     name="Apple Inc."),
        SentimentResult(ticker="AAPL", mention_count=12, avg_sentiment=0.4,
                        news_count=3, avg_news_sentiment=0.3),
    )
    meme = eng.score_candidate(
        "MEME",
        Fundamentals(ticker="MEME", revenue_growth=-0.2, earnings_growth=-0.3,
                     profit_margin=-0.1, roe=-0.05, debt_to_equity=2.8,
                     free_cash_flow=-5e8, trailing_pe=-5.0, name="Meme Co"),
        SentimentResult(ticker="MEME", mention_count=80, avg_sentiment=0.9),
    )
    ranked, _ = eng.rank(
        {"AAPL": strong.fundamentals, "MEME": meme.fundamentals},
        {"AAPL": strong.sentiment, "MEME": meme.sentiment},
    )
    return ranked


def test_report_has_required_sections_text():
    ranked = _scored()
    report = ReportBuilder(top_n=10).build(ranked, run_date="2026-06-25",
                                            stats={"candidates": 2,
                                                   "reddit_posts": 50,
                                                   "news_articles": 10})
    t = report.text_body
    assert "2026-06-25" in t
    assert "AAPL" in t
    assert "fundamentals" in t.lower()
    assert "sentiment" in t.lower()
    assert "not financial advice" in t.lower()
    # rationale + signals present
    assert "Score" in t
    assert "#1" in t


def test_report_has_required_sections_html():
    ranked = _scored()
    report = ReportBuilder(top_n=10).build(ranked, run_date="2026-06-25")
    h = report.html_body
    assert h.startswith("<html>")
    assert "AAPL" in h
    assert "Risks" in h or "risk" in h.lower()
    assert "Supporting signals" in h
    assert "not financial advice" in h.lower()
    assert "</html>" in h


def test_hype_gated_badge_shown():
    ranked = _scored()
    report = ReportBuilder(top_n=10).build(ranked)
    assert "HYPE-GATED" in report.html_body
    assert "HYPE-GATED" in report.text_body


def test_subject_summarizes_top_picks():
    ranked = _scored()
    report = ReportBuilder(top_n=10).build(ranked, run_date="2026-06-25")
    assert "2026-06-25" in report.subject
    assert "AAPL" in report.subject


def test_top_n_limits_picks():
    ranked = _scored()
    report = ReportBuilder(top_n=1).build(ranked)
    # Only the #1 pick should appear in the body
    assert "#1" in report.text_body
    assert "#2" not in report.text_body


def test_empty_picks_handled():
    report = ReportBuilder().build([], run_date="2026-06-25",
                                   excluded=[ScoredCandidate(
                                       ticker="X", final_score=0,
                                       fundamentals_score=0, sentiment_score=0,
                                       fundamentals=Fundamentals(ticker="X"))])
    assert "no qualifying picks" in report.subject.lower()
    assert "Excluded" in report.html_body
    assert "X" in report.text_body


def _pick_with_news():
    eng = ScoringEngine()
    c = eng.score_candidate(
        "AAPL",
        Fundamentals(ticker="AAPL", revenue_growth=0.15, earnings_growth=0.20,
                     profit_margin=0.25, roe=0.30, debt_to_equity=0.4,
                     free_cash_flow=1e11, trailing_pe=28.0, peg_ratio=1.5,
                     current_price=190.0, target_mean_price=210.0,
                     market_cap=3.0e12, sector="Technology", name="Apple Inc."),
        SentimentResult(ticker="AAPL", mention_count=12, avg_sentiment=0.4),
    )
    c.rank = 1
    c.news = [
        NewsRef(title="Apple unveils new chip", source="Reuters",
                url="https://example.com/a"),
        NewsRef(title="Analysts raise Apple target", source="Bloomberg",
                url="https://example.com/b"),
    ]
    return c


def test_key_stats_header_present():
    report = ReportBuilder().build([_pick_with_news()], run_date="2026-06-25")
    # Headline strip surfaces sector, price, cap, upside.
    assert "Technology" in report.html_body
    assert "Technology" in report.text_body
    assert "to target" in report.html_body  # upside vs analyst target
    assert "$3.00T cap" in report.text_body


def test_recent_news_rendered_with_links():
    report = ReportBuilder().build([_pick_with_news()], run_date="2026-06-25")
    assert "Recent news" in report.html_body
    assert "Apple unveils new chip" in report.html_body
    assert "https://example.com/a" in report.html_body  # linked
    # Plain-text variant lists the headline too.
    assert "Recent news" in report.text_body
    assert "Apple unveils new chip" in report.text_body
