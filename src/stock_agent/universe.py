"""Analysis universe selection (roadmap item #5).

Historically the candidate set *was* whatever Reddit/StockTwits/news happened to
mention, which biases the agent toward already-hyped megacaps and meme names and
means a quietly excellent company is never even looked at.

``FixedUniverse`` inverts that: the universe is a fixed reference index (the
vendored S&P 500). Social mentions become a *prioritization overlay* -- they
decide which index names get screened first when we can't afford to score the
whole index every run -- but they are no longer the gatekeeper. Because a daily
run can only fetch fundamentals for a bounded number of tickers (cost/time), the
remaining slots rotate deterministically by date so the entire index is covered
over a couple of weeks.

Selection priority (highest first), capped at ``max_candidates`` but never
dropping the watchlist:

  1. Watchlist           -- explicit user interest (also the escape hatch for
                            off-index names).
  2. Social overlay      -- index names mentioned this run, by mention count.
  3. Rotating coverage   -- the rest of the index, windowed by run date.

The selector is pure and deterministic given its inputs, so it is fully unit
testable with no network.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Iterable, Optional, Sequence

from .universe_data import SP500


class FixedUniverse:
    def __init__(self, tickers: Optional[Sequence[str]] = None) -> None:
        # Preserve a stable, de-duplicated order for reproducible rotation.
        seen: set[str] = set()
        ordered: list[str] = []
        for t in (tickers if tickers is not None else SP500):
            u = t.strip().upper()
            if u and u not in seen:
                seen.add(u)
                ordered.append(u)
        self._tickers: tuple[str, ...] = tuple(ordered)
        self._member: frozenset[str] = frozenset(ordered)

    def __len__(self) -> int:
        return len(self._tickers)

    def __contains__(self, ticker: str) -> bool:
        return ticker.strip().upper() in self._member

    @property
    def tickers(self) -> tuple[str, ...]:
        return self._tickers

    def select(
        self,
        mention_counts: Optional[Counter] = None,
        watchlist: Optional[Iterable[str]] = None,
        max_candidates: Optional[int] = None,
        run_date: Optional[str] = None,
    ) -> list[str]:
        """Return the ordered candidate universe for one run.

        ``mention_counts`` is the social/news mention Counter (overlay only);
        only counts for *index members* influence ordering -- off-index hype is
        intentionally ignored (the watchlist is the escape hatch).
        """
        counts = mention_counts or Counter()
        watch = [t.strip().upper() for t in (watchlist or []) if t.strip()]

        selected: list[str] = []
        seen: set[str] = set()

        def add(t: str) -> None:
            if t not in seen:
                seen.add(t)
                selected.append(t)

        # 1. Watchlist always in (exempt from the cap).
        for t in watch:
            add(t)

        # 2. Social overlay: index members mentioned this run, by count desc
        #    then alphabetical for stability.
        overlay = sorted(
            (t for t in self._member if counts.get(t, 0) > 0),
            key=lambda t: (-counts.get(t, 0), t),
        )

        # 3. Rotating coverage of the remaining index, windowed by date so the
        #    whole index is screened over time.
        overlay_set = set(overlay)
        rest = [t for t in self._tickers if t not in overlay_set]
        rest = self._rotate(rest, run_date)

        # Fill up to the cap from overlay first, then rotating coverage. The
        # watchlist already consumed some slots but is never evicted.
        if max_candidates is None:
            for t in overlay:
                add(t)
            for t in rest:
                add(t)
            return selected

        for t in overlay + rest:
            if len(selected) >= max_candidates:
                break
            add(t)
        return selected

    @staticmethod
    def _rotate(items: list[str], run_date: Optional[str]) -> list[str]:
        """Rotate the list by a date-derived offset for deterministic coverage."""
        if not items:
            return items
        if run_date:
            try:
                offset = date.fromisoformat(run_date).toordinal()
            except ValueError:
                offset = sum(ord(c) for c in run_date)
        else:
            offset = date.today().toordinal()
        # Step by a window each day so consecutive days screen different slices.
        start = (offset * 25) % len(items)
        return items[start:] + items[:start]
