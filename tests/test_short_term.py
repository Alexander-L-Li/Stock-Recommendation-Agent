"""Tests for the short-term (1-3 month) evaluator."""
import math

from stock_agent.models import Fundamentals, SentimentResult
from stock_agent.scoring.short_term import (
    ShortTermEvaluator,
    TechnicalSignals,
    compute_technicals,
    _rsi,
    _ret,
    _sma,
)


def _series(rate, n=260, start=100.0):
    """Geometric daily series at constant daily ``rate`` (e.g. 0.004 = +0.4%/d)."""
    return [start * ((1.0 + rate) ** i) for i in range(n)]


# ---------------- helpers ----------------
def test_ret_and_sma_and_rsi_basics():
    closes = _series(0.0)  # flat
    assert _ret(closes, 21) == 0.0
    assert abs(_sma(closes, 50) - 100.0) < 1e-9
    # Flat -> no gains/losses -> RSI neutral 50 by our convention.
    assert _rsi(closes) == 50.0

    up = _series(0.01, n=40)
    assert _ret(up, 21) > 0
    assert _rsi(up) == 100.0  # only gains


def test_compute_technicals_uptrend_fields():
    closes = _series(0.004)
    t = compute_technicals("UP", closes, [2e6] * len(closes))
    assert t.error is None
    assert t.ret_1m and t.ret_3m and t.ret_1m > 0
    assert t.sma50 and t.sma200 and t.sma50 > t.sma200  # rising -> fast above slow
    assert t.uptrend is True
    assert t.above_sma50 is True
    assert t.pct_from_52w_high is not None and t.pct_from_52w_high <= 0.0
    assert t.avg_dollar_volume is not None


def test_compute_technicals_insufficient_history():
    t = compute_technicals("NA", [100, 101, 102])
    assert t.error == "insufficient history"


# ---------------- signals ----------------
def test_strong_uptrend_with_buzz_scores_buy_or_strong():
    ev = ShortTermEvaluator()
    closes = _series(0.0035)
    t = compute_technicals("MO", closes, [5e6] * len(closes))
    res = ev.score_candidate(
        "MO", t,
        SentimentResult("MO", mention_count=25, avg_sentiment=0.5,
                        news_count=6, avg_news_sentiment=0.4),
        Fundamentals("MO", earnings_growth=0.30, revenue_growth=0.20),
    )
    assert res.signal in ("BUY", "STRONG BUY")
    assert res.score >= 60
    assert "momentum" in res.subs and res.subs["momentum"] > 50
    assert any("momentum" in r for r in res.reasons)


def test_falling_knife_is_penalized_and_avoided():
    ev = ShortTermEvaluator()
    # Long mild downtrend, then a sharp recent drop -> below SMA50, neg 1m.
    closes = _series(-0.002, n=240) + [80 * (0.97 ** i) for i in range(20)]
    t = compute_technicals("KN", closes, [3e6] * len(closes))
    res = ev.score_candidate("KN", t)
    assert res.signal == "AVOID"
    assert any("knife" in r.lower() for r in res.risks)


def test_liquidity_gate_caps_score():
    ev = ShortTermEvaluator()  # default min_dollar_volume = 2_000_000
    closes = _series(0.004)
    # Strong technicals but tiny dollar volume.
    t = compute_technicals("THIN", closes, [1000] * len(closes))
    res = ev.score_candidate(
        "THIN", t,
        SentimentResult("THIN", mention_count=20, avg_sentiment=0.6),
    )
    assert res.score <= 50.0
    assert any("liquidity" in r.lower() for r in res.risks)


def test_no_history_returns_avoid_zero():
    ev = ShortTermEvaluator()
    t = compute_technicals("NA", [100, 101])
    res = ev.score_candidate("NA", t)
    assert res.signal == "AVOID"
    assert res.score == 0.0
    assert "momentum" not in res.subs


# ---------------- robustness ----------------
def test_weights_renormalize_when_components_missing():
    """A name with only price data (no sentiment/fundamentals) still scores on
    the present components rather than being dragged toward zero."""
    ev = ShortTermEvaluator()
    closes = _series(0.003)
    t = compute_technicals("PX", closes, [4e6] * len(closes))
    res = ev.score_candidate("PX", t)  # no sentiment, no fundamentals
    assert "sentiment" not in res.subs
    assert "earnings" not in res.subs
    assert res.score > 50  # momentum/trend/posture carry it
    assert math.isfinite(res.score)


def test_stronger_setup_outscores_flat_setup():
    ev = ShortTermEvaluator()
    flat = TechnicalSignals(ticker="X", ret_1m=0.0, ret_3m=0.0, price=100.0,
                            sma50=100.0, sma200=100.0, rsi14=55.0,
                            pct_from_52w_high=-0.05, vol_20d=0.3,
                            avg_dollar_volume=1e8)
    weak = ev.score_candidate("X", flat)
    strong_t = TechnicalSignals(ticker="Y", ret_1m=0.15, ret_3m=0.30,
                                price=120.0, sma50=110.0, sma200=100.0,
                                rsi14=60.0, pct_from_52w_high=-0.02,
                                vol_20d=0.3, volume_ratio=1.4,
                                avg_dollar_volume=1e8)
    strong = ev.score_candidate(
        "Y", strong_t,
        SentimentResult("Y", mention_count=20, avg_sentiment=0.5))
    assert strong.score > weak.score


# ---------------- report rendering of the short-term section ----------------
def test_report_renders_short_term_section():
    from stock_agent.report.builder import ReportBuilder
    from stock_agent.scoring.short_term import ShortTermScore, TechnicalSignals

    st = ShortTermScore(
        ticker="NVDA", score=82.0, base_score=82.0, signal="STRONG BUY",
        subs={"momentum": 90.0}, reasons=["+30% 3-month momentum", "uptrend"],
        risks=["Overbought (RSI 78)"], name="Nvidia",
        technicals=TechnicalSignals(ticker="NVDA", ret_1m=0.1, ret_3m=0.3,
                                    rsi14=78.0, sma50=110, sma200=100,
                                    price=120, pct_from_52w_high=-0.01),
    )
    report = ReportBuilder(top_n=10).build(
        [], run_date="2026-06-25", short_term=[st])

    t = report.text_body
    assert "SHORT-TERM PICKS (1–3 MONTHS)" in t
    assert "[STRONG BUY]  NVDA" in t
    assert "3mo" in t  # technicals line rendered

    h = report.html_body
    assert "Short-term picks (1–3 months)" in h
    assert ">STRONG BUY<" in h
    assert "NVDA" in h


def test_report_omits_short_term_section_when_empty():
    from stock_agent.report.builder import ReportBuilder
    report = ReportBuilder(top_n=10).build([], run_date="2026-06-25")
    assert "SHORT-TERM PICKS" not in report.text_body
    assert "Short-term picks" not in report.html_body

