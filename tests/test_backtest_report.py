"""Tests for the backtest report formatter."""
from stock_agent.analysis.backtest import BacktestResult, HorizonStats, PickReturn
from stock_agent.report.backtest_report import format_backtest_report


def _result(observations=12):
    h = HorizonStats(
        horizon_days=90, n=observations, mean_stock_return=0.08,
        mean_bench_return=0.03, mean_excess_return=0.05,
        median_excess_return=0.04, hit_rate=0.66, win_rate=0.75,
        rank_ic=0.31, top_half_excess=0.09, bottom_half_excess=0.01,
    )
    best = PickReturn("2026-01-01", "AAPL", 1, 90.0, 90, 100.0, 130.0,
                      0.30, 0.03, 0.27)
    worst = PickReturn("2026-01-01", "MEME", 3, 40.0, 90, 50.0, 40.0,
                       -0.20, 0.03, -0.23)
    return BacktestResult(
        as_of="2026-06-25", benchmark="SPY", n_runs=5, n_picks=20,
        n_observations=observations, pending=8, horizons=[h],
        best=best, worst=worst, skipped_no_entry_price=2,
    )


def test_report_has_key_sections_text():
    report = format_backtest_report(_result())
    t = report.text_body
    assert "PERFORMANCE BACKTEST" in t
    assert "SPY" in t
    assert "RankIC" in t or "rank IC" in t
    assert "AAPL" in t  # best pick
    assert "MEME" in t  # worst pick
    assert "not financial advice" in t.lower()


def test_report_has_key_sections_html():
    report = format_backtest_report(_result())
    h = report.html_body
    assert h.startswith("<html>")
    assert "Performance Backtest" in h
    assert "Rank IC" in h
    assert "</html>" in h


def test_empty_history_reports_insufficient_data():
    empty = BacktestResult(as_of="2026-06-25", benchmark="SPY", n_runs=0,
                           n_picks=0, n_observations=0, pending=0)
    report = format_backtest_report(empty)
    assert "Not enough matured history" in report.text_body
    assert report.html_body.startswith("<html>")
