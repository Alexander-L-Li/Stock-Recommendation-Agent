"""Tests for #4: price-based risk/momentum factor fetcher."""
import math

import pytest

from stock_agent.fundamentals.price_factors import (
    PriceFactorFetcher,
    PriceSeries,
    _MONTH,
    _YEAR,
    _daily_returns,
)


def _closes_from_returns(start, returns):
    closes = [start]
    for r in returns:
        closes.append(closes[-1] * (1.0 + r))
    return closes


def _stdev_annualized(returns):
    m = sum(returns) / len(returns)
    var = sum((r - m) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(_YEAR)


def test_insufficient_history_yields_error():
    f = PriceFactorFetcher(series_factory=lambda s: PriceSeries([10.0] * 5, [1] * 5))
    pf = f.fetch("X")
    assert pf.error == "insufficient history"
    assert pf.momentum is None


def test_factory_failure_is_captured():
    def boom(_):
        raise RuntimeError("network down")

    pf = PriceFactorFetcher(series_factory=boom).fetch("X")
    assert pf.error is not None and "network" in pf.error


def test_beta_and_volatility_relative_to_benchmark():
    # Stock daily returns are exactly 2x the benchmark's -> beta == 2.0 and
    # volatility == 2x the benchmark volatility.
    bench_returns = [0.01, -0.005, 0.02, -0.01, 0.015, -0.02] * 50  # 300 days
    stock_returns = [2 * r for r in bench_returns]
    bench = PriceSeries(_closes_from_returns(100.0, bench_returns), [1000] * 301)
    stock = PriceSeries(_closes_from_returns(50.0, stock_returns), [2000] * 301)

    def factory(sym):
        return bench if sym == "SPY" else stock

    pf = PriceFactorFetcher(series_factory=factory, benchmark="SPY").fetch("TICK")
    assert pf.error is None
    assert pf.beta == pytest.approx(2.0, rel=1e-6)
    bench_vol = _stdev_annualized(_daily_returns(bench.closes))
    assert pf.volatility == pytest.approx(2 * bench_vol, rel=1e-6)


def test_momentum_positive_for_uptrend_and_matches_formula():
    closes = _closes_from_returns(100.0, [0.004] * (_YEAR + 40))
    series = PriceSeries(closes, [1000] * len(closes))
    pf = PriceFactorFetcher(series_factory=lambda s: series).fetch("UP")
    assert pf.momentum is not None and pf.momentum > 0
    # Recompute with the same indices the implementation uses (12-1 month).
    recent = closes[-_MONTH - 1]
    past = closes[len(closes) - 1 - _YEAR]
    assert pf.momentum == pytest.approx(recent / past - 1.0)


def test_max_drawdown_and_liquidity():
    # Rise to 120 then fall to 90 -> drawdown -25%.
    closes = ([100 + i for i in range(21)]            # 100..120
              + [120 - 3 * i for i in range(1, 11)])  # 117..90
    series = PriceSeries([float(c) for c in closes], [100.0] * len(closes))
    pf = PriceFactorFetcher(series_factory=lambda s: series).fetch("DD")
    assert pf.max_drawdown == pytest.approx(90 / 120 - 1.0)  # -0.25
    # Liquidity = mean(close*volume) over the last month window.
    n = min(_MONTH, len(closes))
    expected = sum(c * 100.0 for c in [float(x) for x in closes][-n:]) / n
    assert pf.avg_dollar_volume == pytest.approx(expected)


def test_cache_avoids_refetch():
    calls = {"n": 0}

    def factory(sym):
        calls["n"] += 1
        return PriceSeries([100.0 + i for i in range(60)], [1.0] * 60)

    f = PriceFactorFetcher(series_factory=factory)
    f.fetch("X")
    f.fetch("X")
    # One call for X plus one for the benchmark; the second fetch hits cache.
    assert calls["n"] <= 2
