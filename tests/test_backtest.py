"""Tests for the backtest engine and its pure-python stats helpers."""
import pytest

from stock_agent.analysis.backtest import (
    BacktestEngine,
    _spearman,
)


class FakeStore:
    """Minimal store exposing the two methods the engine needs."""

    def __init__(self, runs):
        # runs: {run_date: [pick_dict, ...]}
        self._runs = runs

    def list_run_dates(self):
        return sorted(self._runs, reverse=True)

    def get_run(self, run_date):
        return {"meta": {}, "picks": self._runs.get(run_date, [])}


class FakePrices:
    """close_on_or_after over an in-memory {ticker: {date: price}} table."""

    def __init__(self, table):
        self._table = {t: sorted(d.items()) for t, d in table.items()}

    def close_on_or_after(self, ticker, day):
        for d, p in self._table.get(ticker.upper(), []):
            if d >= day:
                return p
        return None


def _pick(ticker, rank, score, entry_price):
    return {"ticker": ticker, "rank": rank, "final_score": score,
            "entry_price": entry_price}


def _engine():
    runs = {
        "2026-01-01": [
            _pick("AAPL", 1, 90.0, 100.0),
            _pick("MSFT", 2, 70.0, 200.0),
            _pick("MEME", 3, 40.0, 50.0),
        ]
    }
    prices = {
        "AAPL": {"2026-01-01": 100.0, "2026-01-31": 120.0},   # +20%
        "MSFT": {"2026-01-01": 200.0, "2026-01-31": 220.0},   # +10%
        "MEME": {"2026-01-01": 50.0, "2026-01-31": 45.0},     # -10%
        "SPY": {"2026-01-01": 400.0, "2026-01-31": 408.0},    # +2%
    }
    return BacktestEngine(FakeStore(runs), FakePrices(prices),
                          horizons=(30, 365), benchmark="SPY")


def test_forward_returns_and_excess():
    result = _engine().run(today="2026-03-01")
    assert result.n_runs == 1
    assert result.n_picks == 3
    # Only the 30-day horizon has matured by 2026-03-01.
    assert [h.horizon_days for h in result.horizons] == [30]
    h = result.horizons[0]
    assert h.n == 3
    assert abs(h.mean_stock_return - (0.20 + 0.10 - 0.10) / 3) < 1e-9
    assert abs(h.mean_excess_return - (0.18 + 0.08 - 0.12) / 3) < 1e-9


def test_hit_and_win_rates():
    h = _engine().run(today="2026-03-01").horizons[0]
    assert abs(h.hit_rate - 2 / 3) < 1e-9
    assert abs(h.win_rate - 2 / 3) < 1e-9


def test_rank_ic_is_positive_when_scores_predict_returns():
    h = _engine().run(today="2026-03-01").horizons[0]
    assert h.rank_ic == pytest.approx(1.0)


def test_top_half_beats_bottom_half():
    h = _engine().run(today="2026-03-01").horizons[0]
    assert h.top_half_excess > h.bottom_half_excess


def test_best_and_worst_picks():
    result = _engine().run(today="2026-03-01")
    assert result.best.ticker == "AAPL"
    assert result.worst.ticker == "MEME"


def test_unmatured_horizon_is_pending_not_counted():
    result = _engine().run(today="2026-03-01")
    assert result.pending == 3
    assert all(h.horizon_days != 365 for h in result.horizons)


def test_picks_without_entry_price_are_skipped():
    runs = {"2026-01-01": [
        {"ticker": "AAPL", "rank": 1, "final_score": 90.0, "entry_price": 100.0},
        {"ticker": "OLD", "rank": 2, "final_score": 80.0},  # no entry_price
    ]}
    prices = {
        "AAPL": {"2026-01-01": 100.0, "2026-01-31": 110.0},
        "SPY": {"2026-01-01": 400.0, "2026-01-31": 400.0},
    }
    result = BacktestEngine(FakeStore(runs), FakePrices(prices),
                            horizons=(30,)).run(today="2026-03-01")
    assert result.n_picks == 1
    assert result.skipped_no_entry_price == 1


def test_missing_future_price_yields_no_observations():
    runs = {"2026-01-01": [_pick("AAPL", 1, 90.0, 100.0)]}
    prices = {"AAPL": {"2026-01-01": 100.0}, "SPY": {"2026-01-01": 400.0}}
    result = BacktestEngine(FakeStore(runs), FakePrices(prices),
                            horizons=(30,)).run(today="2026-03-01")
    assert result.n_observations == 0
    assert result.horizons == []


def test_spearman_helper():
    assert _spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert _spearman([1, 2, 3], [30, 20, 10]) == pytest.approx(-1.0)
    assert _spearman([1, 2], [1, 2]) is None  # too few points
