"""Backtesting + performance attribution (#1).

Reads the point-in-time recommendation history persisted by the store (each pick
records its as-of ``entry_price``; see ``storage/dynamo._feature_snapshot``) and
measures how those picks actually performed against a benchmark (SPY by default).

Why this matters for credibility
---------------------------------
The daily agent emits scores, but a score is only trustworthy if it predicts
realized forward returns. This module turns the stored history into an evidenced
track record:

* **Forward return** of each pick over fixed horizons (30/90/180/365 calendar
  days), measured from the recorded entry price — no look-ahead, because the
  entry price was captured at recommendation time.
* **Excess return** vs the benchmark over the same window (alpha, not beta).
* **Hit rate** (share of picks that beat the benchmark) and **win rate** (share
  with a positive absolute return).
* **Rank IC** — the Spearman rank correlation between the model's ``final_score``
  and the realized forward return. This is the single most important number: a
  persistently positive IC means the ranking carries genuine predictive signal;
  ~0 means the scores are noise.
* **Top-half vs bottom-half excess** — does ranking higher actually pay off?

Everything is pure-python (no numpy/scipy needed) and the price source is
injected, so the engine is fully unit-testable without network access.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)

DEFAULT_HORIZONS = (30, 90, 180, 365)


class PriceProvider(Protocol):
    """Returns the first available closing price on or after ``day`` (ISO date).

    "On or after" handles weekends/holidays: a recommendation dated on a Friday
    is exited at the next trading day's close when a horizon lands on a weekend.
    Returns ``None`` when no price is available (e.g. delisted, or the horizon
    extends past the data).
    """

    def close_on_or_after(self, ticker: str, day: str) -> Optional[float]:
        ...


# --------------------------- result models ---------------------------
@dataclass
class PickReturn:
    run_date: str
    ticker: str
    rank: int
    final_score: float
    horizon_days: int
    entry_price: float
    exit_price: float
    stock_return: float
    bench_return: float
    excess_return: float


@dataclass
class HorizonStats:
    horizon_days: int
    n: int
    mean_stock_return: float
    mean_bench_return: float
    mean_excess_return: float
    median_excess_return: float
    hit_rate: float              # share with excess_return > 0 (beat benchmark)
    win_rate: float              # share with stock_return > 0 (made money)
    rank_ic: Optional[float]     # Spearman(final_score, stock_return)
    top_half_excess: Optional[float]
    bottom_half_excess: Optional[float]


@dataclass
class BacktestResult:
    as_of: str
    benchmark: str
    n_runs: int
    n_picks: int                 # distinct (run, ticker) picks with an entry price
    n_observations: int          # (pick, matured-horizon) pairs evaluated
    pending: int                 # (pick, horizon) pairs not yet matured
    horizons: list[HorizonStats] = field(default_factory=list)
    best: Optional[PickReturn] = None
    worst: Optional[PickReturn] = None
    skipped_no_entry_price: int = 0


# --------------------------- stats helpers ---------------------------
def _ranks(values: list[float]) -> list[float]:
    """Average (tie-corrected) ranks, 1-based."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _spearman(xs: list[float], ys: list[float]) -> Optional[float]:
    """Rank correlation in [-1, 1]; None when undefined (n<3 or no variance)."""
    if len(xs) < 3:
        return None
    return _pearson(_ranks(xs), _ranks(ys))


# --------------------------- engine ---------------------------
class BacktestEngine:
    def __init__(self, store: Any, price_provider: PriceProvider,
                 horizons: tuple[int, ...] = DEFAULT_HORIZONS,
                 benchmark: str = "SPY") -> None:
        self.store = store
        self.price = price_provider
        self.horizons = tuple(sorted(horizons))
        self.benchmark = benchmark

    def run(self, today: Optional[str] = None) -> BacktestResult:
        today = today or date.today().isoformat()
        run_dates = self.store.list_run_dates()

        obs_by_h: dict[int, list[PickReturn]] = {h: [] for h in self.horizons}
        all_obs: list[PickReturn] = []
        seen_runs: set[str] = set()
        n_picks = 0
        pending = 0
        skipped = 0

        for rd in run_dates:
            picks = self.store.get_run(rd).get("picks", [])
            bench_entry = self.price.close_on_or_after(self.benchmark, rd)
            entry_date = date.fromisoformat(rd)
            for p in picks:
                entry_price = p.get("entry_price")
                if entry_price is None or entry_price <= 0:
                    skipped += 1
                    continue
                n_picks += 1
                seen_runs.add(rd)
                ticker = p["ticker"]
                score = float(p.get("final_score", 0.0))
                rank = int(p.get("rank", 0))

                for h in self.horizons:
                    target = (entry_date + timedelta(days=h)).isoformat()
                    if target > today:
                        pending += 1
                        continue
                    exit_price = self.price.close_on_or_after(ticker, target)
                    if exit_price is None or exit_price <= 0:
                        continue
                    stock_ret = exit_price / entry_price - 1.0
                    bench_ret = 0.0
                    if bench_entry:
                        bench_exit = self.price.close_on_or_after(
                            self.benchmark, target)
                        if bench_exit:
                            bench_ret = bench_exit / bench_entry - 1.0
                    pr = PickReturn(
                        run_date=rd, ticker=ticker, rank=rank,
                        final_score=score, horizon_days=h,
                        entry_price=entry_price, exit_price=exit_price,
                        stock_return=stock_ret, bench_return=bench_ret,
                        excess_return=stock_ret - bench_ret,
                    )
                    obs_by_h[h].append(pr)
                    all_obs.append(pr)

        horizons_stats = [self._horizon_stats(h, obs_by_h[h])
                          for h in self.horizons if obs_by_h[h]]
        best = max(all_obs, key=lambda o: o.excess_return, default=None)
        worst = min(all_obs, key=lambda o: o.excess_return, default=None)

        return BacktestResult(
            as_of=today, benchmark=self.benchmark, n_runs=len(seen_runs),
            n_picks=n_picks, n_observations=len(all_obs), pending=pending,
            horizons=horizons_stats, best=best, worst=worst,
            skipped_no_entry_price=skipped,
        )

    @staticmethod
    def _horizon_stats(horizon: int, obs: list[PickReturn]) -> HorizonStats:
        n = len(obs)
        stock = [o.stock_return for o in obs]
        bench = [o.bench_return for o in obs]
        excess = [o.excess_return for o in obs]
        scores = [o.final_score for o in obs]

        ranked = sorted(obs, key=lambda o: o.final_score, reverse=True)
        half = n // 2
        top = ranked[:half]
        bottom = ranked[half:] if half else []
        top_ex = statistics.fmean([o.excess_return for o in top]) if top else None
        bot_ex = (statistics.fmean([o.excess_return for o in bottom])
                  if bottom else None)

        return HorizonStats(
            horizon_days=horizon,
            n=n,
            mean_stock_return=statistics.fmean(stock),
            mean_bench_return=statistics.fmean(bench),
            mean_excess_return=statistics.fmean(excess),
            median_excess_return=statistics.median(excess),
            hit_rate=sum(1 for e in excess if e > 0) / n,
            win_rate=sum(1 for r in stock if r > 0) / n,
            rank_ic=_spearman(scores, stock),
            top_half_excess=top_ex,
            bottom_half_excess=bot_ex,
        )


# --------------------------- real price provider ---------------------------
class YFinancePriceProvider:
    """PriceProvider backed by yfinance daily closes (auto-adjusted).

    Fetches each symbol's history once and caches it; ``close_on_or_after`` then
    does an in-memory lookup. The yfinance ``Ticker`` factory is injectable so
    this class can be exercised without network in tests if desired.
    """

    def __init__(self, ticker_factory: Optional[Any] = None) -> None:
        self._factory = ticker_factory or self._default_factory
        self._cache: dict[str, list[tuple[str, float]]] = {}

    @staticmethod
    def _default_factory(symbol: str) -> Any:
        import yfinance as yf

        return yf.Ticker(symbol)

    def _history(self, ticker: str) -> list[tuple[str, float]]:
        ticker = ticker.upper()
        if ticker not in self._cache:
            series: list[tuple[str, float]] = []
            try:
                obj = self._factory(ticker)
                df = obj.history(period="max", auto_adjust=True)
                for idx, row in df.iterrows():
                    close = row.get("Close")
                    if close is None or close != close:  # NaN guard
                        continue
                    iso = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
                    series.append((iso, float(close)))
                series.sort()
            except Exception as exc:  # network/parse failures are non-fatal
                logger.warning("Price history fetch failed for %s: %s",
                               ticker, exc)
            self._cache[ticker] = series
        return self._cache[ticker]

    def close_on_or_after(self, ticker: str, day: str) -> Optional[float]:
        for iso, close in self._history(ticker):
            if iso >= day:
                return close
        return None
