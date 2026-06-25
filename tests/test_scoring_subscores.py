from stock_agent.config import Config
from stock_agent.scoring.engine import ScoringEngine
from stock_agent.models import Fundamentals, SentimentResult


def _strong():
    return Fundamentals(
        ticker="STRONG", revenue_growth=0.25, earnings_growth=0.30,
        profit_margin=0.25, roe=0.30, debt_to_equity=0.3,
        free_cash_flow=1e10, trailing_pe=18.0, peg_ratio=1.0,
        current_price=100.0, target_mean_price=120.0, name="Strong Co",
    )


def _weak():
    return Fundamentals(
        ticker="WEAK", revenue_growth=-0.05, earnings_growth=-0.10,
        profit_margin=0.02, roe=0.01, debt_to_equity=3.0,
        free_cash_flow=-1e9, trailing_pe=80.0, peg_ratio=4.0,
        name="Weak Co",
    )


def test_strong_fundamentals_score_high():
    eng = ScoringEngine()
    score, subs = eng.score_fundamentals(_strong())
    assert score > 75
    assert len(subs) == 7


def test_weak_fundamentals_score_low():
    eng = ScoringEngine()
    score, _ = eng.score_fundamentals(_weak())
    assert score < 30


def test_fundamentals_renormalize_partial_metrics():
    eng = ScoringEngine()
    # Only two metrics present; both excellent -> high score, not diluted by None.
    f = Fundamentals(ticker="P", revenue_growth=0.30, roe=0.30)
    score, subs = eng.score_fundamentals(f)
    assert set(subs) == {"revenue_growth", "roe"}
    assert score > 90


def test_sentiment_neutral_when_no_mentions():
    eng = ScoringEngine()
    assert eng.score_sentiment(None) == 50.0
    assert eng.score_sentiment(SentimentResult(ticker="X")) == 50.0


def test_sentiment_positive_above_neutral():
    eng = ScoringEngine()
    s = SentimentResult(ticker="X", mention_count=10, avg_sentiment=0.6)
    assert eng.score_sentiment(s) > 60


def test_sentiment_negative_below_neutral():
    eng = ScoringEngine()
    s = SentimentResult(ticker="X", mention_count=10, avg_sentiment=-0.6)
    assert eng.score_sentiment(s) < 40
