"""Price-based risk & momentum factor fetcher (roadmap item #4).

Computes a small, well-understood set of price factors from daily history:

* **momentum** -- 12-1 month total return (last ~12 months excluding the most
  recent ~1 month), the classic cross-sectional momentum definition that skips
  the short-term reversal window.
* **volatility** -- annualized standard deviation of daily returns.
* **beta** -- sensitivity to the benchmark (SPY) over the overlapping window.
* **max_drawdown** -- worst peak-to-trough decline over the window (<= 0).
* **avg_dollar_volume** -- mean daily dollar volume (a liquidity proxy).

Stats are pure Python over plain lists, so the unit is fully testable without
numpy/pandas. The price source is injected via ``series_factory`` (a callable
``symbol -> Optional[PriceSeries]``); the default wraps ``yfinance`` and is the
only part that touches the network.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..models import PriceFactors

logger = logging.getLogger(__name__)

# ~21 trading days per month, ~252 per year.
_MONTH = 21
_YEAR = 252


@dataclass
class PriceSeries:
    """Daily closes + volumes for a symbol, oldest-first."""

    closes: list[float]
    volumes: list[float]


def _default_series_factory(symbol: str) -> Optional[PriceSeries]:
    import yfinance as yf

    hist = yf.Ticker(symbol).history(period="2y", auto_adjust=True)
    if hist is None or len(hist) == 0:
        return None
    closes = [float(x) for x in hist["Close"].tolist()
              if x is not None and math.isfinite(float(x))]
    vols = [float(x) for x in hist["Volume"].tolist()
            if x is not None and math.isfinite(float(x))]
    if not closes:
        return None
    return PriceSeries(closes=closes, volumes=vols)


def _daily_returns(closes: list[float]) -> list[float]:
    out: list[float] = []
    for prev, cur in zip(closes, closes[1:]):
        if prev and math.isfinite(prev) and prev != 0:
            out.append(cur / prev - 1.0)
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: list[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


class PriceFactorFetcher:
    def __init__(
        self,
        series_factory: Optional[Callable[[str], Optional[PriceSeries]]] = None,
        benchmark: str = "SPY",
        cache_ttl_seconds: float = 3600.0,
    ) -> None:
        self._factory = series_factory or _default_series_factory
        self.benchmark = benchmark
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, PriceFactors]] = {}
        self._bench_returns: Optional[list[float]] = None
        self._bench_loaded = False

    # ---------------- public ----------------
    def fetch(self, ticker: str) -> PriceFactors:
        ticker = ticker.strip().upper()
        now = time.time()
        cached = self._cache.get(ticker)
        if cached and (now - cached[0]) < self._cache_ttl:
            return cached[1]
        try:
            series = self._factory(ticker)
            if series is None or len(series.closes) < _MONTH + 5:
                factors = PriceFactors(ticker=ticker, error="insufficient history")
            else:
                factors = self._compute(ticker, series)
        except Exception as exc:  # network / parse failures are non-fatal
            logger.warning("Price factors failed for %s: %s", ticker, exc)
            factors = PriceFactors(ticker=ticker, error=str(exc))
        self._cache[ticker] = (now, factors)
        return factors

    def fetch_many(self, tickers: list[str]) -> dict[str, PriceFactors]:
        return {t: self.fetch(t) for t in tickers}

    # ---------------- internals ----------------
    def _benchmark_returns(self) -> Optional[list[float]]:
        if not self._bench_loaded:
            self._bench_loaded = True
            try:
                series = self._factory(self.benchmark)
                if series and len(series.closes) >= 2:
                    self._bench_returns = _daily_returns(series.closes)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Benchmark %s load failed: %s",
                               self.benchmark, exc)
        return self._bench_returns

    def _compute(self, ticker: str, series: PriceSeries) -> PriceFactors:
        closes = series.closes
        rets = _daily_returns(closes)

        # Momentum: 12-1 month. Prefer a full year of skip-a-month lookback;
        # gracefully shorten when history is limited.
        momentum: Optional[float] = None
        if len(closes) > _MONTH + 1:
            recent = closes[-_MONTH - 1]  # ~1 month ago (skip reversal window)
            lookback_idx = len(closes) - 1 - _YEAR
            past = closes[lookback_idx] if lookback_idx >= 0 else closes[0]
            if past:
                momentum = recent / past - 1.0

        # Volatility: annualized daily-return stdev.
        sd = _stdev(rets)
        volatility = sd * math.sqrt(_YEAR) if sd is not None else None

        # Beta vs benchmark over the overlapping (most-recent) window.
        beta = self._beta(rets)

        # Max drawdown over the window.
        max_drawdown = self._max_drawdown(closes)

        # Liquidity: mean dollar volume over the last month.
        avg_dollar_volume: Optional[float] = None
        if series.volumes:
            n = min(_MONTH, len(series.volumes), len(closes))
            pairs = list(zip(closes[-n:], series.volumes[-n:]))
            if pairs:
                avg_dollar_volume = _mean([c * v for c, v in pairs])

        return PriceFactors(
            ticker=ticker,
            momentum=momentum,
            volatility=volatility,
            beta=beta,
            max_drawdown=max_drawdown,
            avg_dollar_volume=avg_dollar_volume,
        )

    def _beta(self, rets: list[float]) -> Optional[float]:
        bench = self._benchmark_returns()
        if not bench or len(rets) < 2:
            return None
        n = min(len(rets), len(bench))
        if n < 2:
            return None
        a = rets[-n:]
        b = bench[-n:]
        mb = _mean(b)
        var_b = sum((x - mb) ** 2 for x in b) / (n - 1)
        if var_b == 0:
            return None
        ma = _mean(a)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (n - 1)
        return cov / var_b

    @staticmethod
    def _max_drawdown(closes: list[float]) -> Optional[float]:
        window = closes[-_YEAR:] if len(closes) > _YEAR else closes
        if len(window) < 2:
            return None
        peak = window[0]
        worst = 0.0
        for c in window:
            if c > peak:
                peak = c
            if peak > 0:
                dd = c / peak - 1.0
                if dd < worst:
                    worst = dd
        return worst
