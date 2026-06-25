"""Tests for #5: FixedUniverse selection (decoupled from social discovery)."""
from collections import Counter

from stock_agent.universe import FixedUniverse
from stock_agent.universe_data import SP500


def _u(n=12):
    return FixedUniverse([f"S{i:02d}" for i in range(n)])


def test_default_universe_is_sp500():
    u = FixedUniverse()
    assert len(u) == len(SP500) >= 400
    assert "AAPL" in u and "BRK-B" in u


def test_watchlist_always_included_even_off_index():
    u = _u(12)
    picks = u.select(watchlist=["ZZZ", "S03"], max_candidates=3)
    # Off-index watchlist name survives and is never evicted by the cap.
    assert "ZZZ" in picks and "S03" in picks


def test_social_overlay_prioritizes_mentioned_index_names():
    u = _u(20)
    counts = Counter({"S10": 5, "S11": 9, "S12": 2})
    picks = u.select(mention_counts=counts, max_candidates=5)
    # Mentioned index names come first, ordered by mention count desc.
    assert picks[:3] == ["S11", "S10", "S12"]


def test_off_index_social_names_ignored():
    u = _u(12)
    counts = Counter({"HYPE": 99})  # not in the index, not on watchlist
    picks = u.select(mention_counts=counts, max_candidates=5)
    assert "HYPE" not in picks


def test_cap_respected_but_watchlist_exempt():
    u = _u(50)
    picks = u.select(watchlist=["W1", "W2", "W3"], max_candidates=4)
    # Watchlist (3) always in; cap fills the rest -> at least the 3 watchlist.
    assert {"W1", "W2", "W3"}.issubset(set(picks))
    # Non-watchlist coverage is bounded by the cap.
    non_watch = [t for t in picks if t not in {"W1", "W2", "W3"}]
    assert len(non_watch) <= 4


def test_rotation_is_deterministic_and_covers_over_time():
    u = _u(40)
    a1 = u.select(max_candidates=10, run_date="2026-06-25")
    a2 = u.select(max_candidates=10, run_date="2026-06-25")
    b = u.select(max_candidates=10, run_date="2026-07-10")
    assert a1 == a2                      # deterministic for a given date
    assert set(a1) != set(b)             # different dates screen different names
    # Across enough days the whole index gets covered.
    covered: set[str] = set()
    for d in range(1, 28):
        covered |= set(u.select(max_candidates=10,
                                run_date=f"2026-06-{d:02d}"))
    assert covered == set(u.tickers)
