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


def _meme():
    # Weak fundamentals but huge positive hype (meme stock).
    return Fundamentals(
        ticker="MEME", revenue_growth=-0.20, earnings_growth=-0.30,
        profit_margin=-0.10, roe=-0.05, debt_to_equity=2.8,
        free_cash_flow=-5e8, trailing_pe=-5.0, name="Meme Co",
    )


def test_70_30_composite_no_gate():
    eng = ScoringEngine()
    f = _strong()
    s = SentimentResult(ticker="STRONG", mention_count=5, avg_sentiment=0.4)
    cand = eng.score_candidate("STRONG", f, s)
    expected = round(0.7 * cand.fundamentals_score + 0.3 * cand.sentiment_score, 2)
    assert abs(cand.final_score - expected) < 0.5
    assert cand.gated is False


def test_hype_gate_blocks_meme_lift():
    eng = ScoringEngine()
    f = _meme()
    # Massive positive sentiment
    s = SentimentResult(ticker="MEME", mention_count=50, avg_sentiment=0.9)
    cand = eng.score_candidate("MEME", f, s)
    assert cand.gated is True
    # Sentiment must NOT lift final above fundamentals
    assert cand.final_score <= cand.fundamentals_score + 1e-6
    # And it's clearly weaker than the ungated 70/30 blend would be
    blend = 0.7 * cand.fundamentals_score + 0.3 * cand.sentiment_score
    assert cand.final_score < blend


def test_hype_gate_threshold_configurable():
    # Lower the gate so a borderline name is NOT gated.
    eng = ScoringEngine(Config(hype_gate_min_fundamentals=0.0))
    cand = eng.score_candidate("MEME", _meme(),
                               SentimentResult(ticker="MEME", mention_count=10,
                                               avg_sentiment=0.8))
    assert cand.gated is False


def test_ranking_orders_by_final_score():
    eng = ScoringEngine()
    funds = {
        "STRONG": _strong(),
        "MEME": _meme(),
        "MID": Fundamentals(ticker="MID", revenue_growth=0.08,
                            earnings_growth=0.05, profit_margin=0.12,
                            roe=0.14, debt_to_equity=1.0, free_cash_flow=1e8,
                            trailing_pe=22.0, name="Mid Co"),
    }
    sent = {
        "MEME": SentimentResult(ticker="MEME", mention_count=50, avg_sentiment=0.9),
        "STRONG": SentimentResult(ticker="STRONG", mention_count=5,
                                  avg_sentiment=0.3),
    }
    ranked, excluded = eng.rank(funds, sent)
    assert excluded == []
    tickers = [c.ticker for c in ranked]
    assert tickers[0] == "STRONG"
    assert tickers.index("MEME") > tickers.index("MID")
    assert [c.rank for c in ranked] == [1, 2, 3]


def test_data_quality_gate_excludes_sparse_and_errored():
    eng = ScoringEngine(Config(min_fundamental_metrics=3))
    funds = {
        "GOOD": _strong(),
        "SPARSE": Fundamentals(ticker="SPARSE", trailing_pe=15.0),  # 1 metric
        "ERR": Fundamentals(ticker="ERR", error="429 rate limit"),
    }
    ranked, excluded = eng.rank(funds, {})
    assert [c.ticker for c in ranked] == ["GOOD"]
    excluded_tickers = {c.ticker for c in excluded}
    assert excluded_tickers == {"SPARSE", "ERR"}
    err_cand = next(c for c in excluded if c.ticker == "ERR")
    assert any("unavailable" in r.lower() for r in err_cand.risks)


def test_explainability_signals_and_risks():
    eng = ScoringEngine()
    strong = eng.score_candidate("STRONG", _strong(),
                                 SentimentResult(ticker="STRONG", mention_count=5,
                                                 avg_sentiment=0.4))
    assert strong.rationale
    assert any("growth" in s.lower() for s in strong.supporting_signals)
    assert "STRONG" in strong.rationale

    meme = eng.score_candidate("MEME", _meme(),
                               SentimentResult(ticker="MEME", mention_count=50,
                                               avg_sentiment=0.9))
    assert any("hype gate" in r.lower() for r in meme.risks)
    assert any("debt" in r.lower() or "cash flow" in r.lower()
               for r in meme.risks)
