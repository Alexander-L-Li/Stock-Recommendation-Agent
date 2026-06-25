"""Tests for #4: the price-factor risk tilt applied in scoring."""
from stock_agent.config import Config
from stock_agent.models import Fundamentals, PriceFactors
from stock_agent.scoring.engine import ScoringEngine


def _mid():
    # Middling fundamentals so the tilt is clearly visible and not clamped.
    return Fundamentals(
        ticker="MID", revenue_growth=0.08, earnings_growth=0.06,
        profit_margin=0.12, roe=0.14, debt_to_equity=1.0,
        free_cash_flow=1e8, trailing_pe=22.0, sector="Tech",
        current_price=100.0,
    )


def test_strong_momentum_low_vol_lifts_score():
    eng = ScoringEngine(Config(risk_tilt_max=10.0))
    good = PriceFactors(ticker="MID", momentum=0.40, volatility=0.20,
                        max_drawdown=-0.08, avg_dollar_volume=5e7)
    base = eng.score_candidate("MID", _mid(), None).final_score
    lifted = eng.score_candidate("MID", _mid(), None, factors=good).final_score
    assert lifted > base
    assert lifted - base <= 10.0 + 1e-6  # bounded by risk_tilt_max


def test_weak_momentum_high_vol_drops_score():
    eng = ScoringEngine(Config(risk_tilt_max=10.0))
    bad = PriceFactors(ticker="MID", momentum=-0.40, volatility=0.90,
                       max_drawdown=-0.55, avg_dollar_volume=5e7)
    base = eng.score_candidate("MID", _mid(), None).final_score
    dropped = eng.score_candidate("MID", _mid(), None, factors=bad).final_score
    assert dropped < base
    assert base - dropped <= 10.0 + 1e-6


def test_illiquid_name_cannot_be_boosted():
    eng = ScoringEngine(Config(risk_tilt_max=10.0, min_dollar_volume=1e7))
    # Great momentum but tiny dollar volume -> positive tilt capped at 0.
    thin = PriceFactors(ticker="MID", momentum=0.50, volatility=0.15,
                        max_drawdown=-0.05, avg_dollar_volume=1e5)
    base = eng.score_candidate("MID", _mid(), None).final_score
    capped = eng.score_candidate("MID", _mid(), None, factors=thin).final_score
    assert capped <= base + 1e-6
    cand = eng.score_candidate("MID", _mid(), None, factors=thin)
    assert any("liquidity" in r.lower() for r in cand.risks)


def test_factor_error_is_ignored():
    eng = ScoringEngine()
    errored = PriceFactors(ticker="MID", error="no history")
    base = eng.score_candidate("MID", _mid(), None).final_score
    same = eng.score_candidate("MID", _mid(), None, factors=errored).final_score
    assert same == base


def test_disabled_price_factors_no_tilt():
    eng = ScoringEngine(Config(enable_price_factors=False))
    good = PriceFactors(ticker="MID", momentum=0.40, volatility=0.20,
                        max_drawdown=-0.05, avg_dollar_volume=5e7)
    base = eng.score_candidate("MID", _mid(), None).final_score
    same = eng.score_candidate("MID", _mid(), None, factors=good).final_score
    assert same == base


def test_factor_explainability_signals():
    eng = ScoringEngine()
    pf = PriceFactors(ticker="MID", momentum=0.30, volatility=0.60,
                      max_drawdown=-0.40, avg_dollar_volume=5e7)
    cand = eng.score_candidate("MID", _mid(), None, factors=pf)
    assert any("momentum" in s.lower() for s in cand.supporting_signals)
    assert any("volatility" in r.lower() for r in cand.risks)
    assert any("drawdown" in r.lower() for r in cand.risks)
    assert cand.factors is pf
