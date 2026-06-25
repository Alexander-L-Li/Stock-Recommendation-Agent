"""Tests for #3: sector-relative cross-sectional scoring."""
from stock_agent.config import Config
from stock_agent.models import Fundamentals
from stock_agent.scoring.engine import ScoringEngine, _percentile_scores


def _f(ticker, sector, **kw):
    return Fundamentals(ticker=ticker, sector=sector, **kw)


def test_percentile_scores_midrank_and_direction():
    pairs = [("A", 0.1), ("B", 0.2), ("C", 0.3), ("D", 0.4)]
    asc = _percentile_scores(pairs, +1)  # higher better
    assert asc["D"] == 87.5 and asc["A"] == 12.5
    desc = _percentile_scores(pairs, -1)  # lower better -> inverted
    assert desc["A"] == 87.5 and desc["D"] == 12.5


def test_cross_sectional_ranks_within_sector():
    eng = ScoringEngine(Config(sector_min_peers=4))
    funds = {
        "T1": _f("T1", "Tech", roe=0.10, debt_to_equity=2.0),
        "T2": _f("T2", "Tech", roe=0.20, debt_to_equity=1.5),
        "T3": _f("T3", "Tech", roe=0.30, debt_to_equity=1.0),
        "T4": _f("T4", "Tech", roe=0.40, debt_to_equity=0.5),
    }
    ov = eng._cross_sectional_subscores(funds)
    # ROE (higher better): best peer gets top percentile.
    assert ov["T4"]["roe"] == 87.5
    assert ov["T1"]["roe"] == 12.5
    # Debt/equity (lower better): lowest leverage gets top percentile.
    assert ov["T4"]["debt_to_equity"] == 87.5
    assert ov["T1"]["debt_to_equity"] == 12.5


def test_thin_sector_falls_back_to_absolute():
    eng = ScoringEngine(Config(sector_min_peers=4))
    funds = {  # only 3 Tech names -> below the peer floor
        "T1": _f("T1", "Tech", roe=0.10),
        "T2": _f("T2", "Tech", roe=0.20),
        "T3": _f("T3", "Tech", roe=0.30),
    }
    ov = eng._cross_sectional_subscores(funds)
    # No overrides emitted -> score_fundamentals uses absolute scaling.
    assert all("roe" not in m for m in ov.values()) or ov == {}


def test_errored_candidate_excluded_from_cohort():
    eng = ScoringEngine(Config(sector_min_peers=2))
    funds = {
        "T1": _f("T1", "Tech", roe=0.10),
        "T2": _f("T2", "Tech", roe=0.20),
        "ERR": Fundamentals(ticker="ERR", sector="Tech", error="boom"),
    }
    ov = eng._cross_sectional_subscores(funds)
    assert "ERR" not in ov
    # Two valid peers -> percentile computed for them.
    assert ov["T2"]["roe"] == 75.0 and ov["T1"]["roe"] == 25.0


def test_sector_relative_rewards_best_in_sector():
    """A name that is only middling on absolute thresholds but best among its
    sector peers should score higher with cross-sectional scoring than without."""
    sector_peers = {
        f"T{i}": _f(f"T{i}", "Tech", revenue_growth=g, earnings_growth=g,
                    profit_margin=0.05, roe=0.05, debt_to_equity=1.0,
                    trailing_pe=25.0)
        for i, g in enumerate([0.02, 0.04, 0.06, 0.08])
    }
    relative = ScoringEngine(Config(enable_sector_relative=True,
                                    sector_min_peers=4))
    absolute = ScoringEngine(Config(enable_sector_relative=False))
    top = "T3"  # highest growth in the cohort
    rel_ranked, _ = relative.rank(sector_peers, {})
    abs_ranked, _ = absolute.rank(sector_peers, {})
    rel_top = next(c for c in rel_ranked if c.ticker == top)
    abs_top = next(c for c in abs_ranked if c.ticker == top)
    assert rel_top.fundamentals_score > abs_top.fundamentals_score
    # Best-in-sector should rank #1 under cross-sectional scoring.
    assert rel_ranked[0].ticker == top
