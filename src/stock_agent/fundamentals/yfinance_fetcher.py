"""Fundamentals fetcher backed by yfinance.

yfinance is unofficial and occasionally rate-limits or returns partial data, so
this module is defensive:

* Each metric is read independently; a missing key yields ``None`` rather than an
  error, and ``Fundamentals.available_count()`` lets the scorer gate on data
  quality.
* A whole-ticker failure is captured in ``Fundamentals.error`` so the run
  continues for other tickers.
* A simple in-process TTL cache avoids re-fetching the same ticker within a run
  (and across quick retries). For a once-daily batch this is plenty; no external
  cache is needed.

The yfinance dependency is injected via ``ticker_factory`` so tests run without
network access. The factory takes a symbol and returns an object exposing an
``info`` mapping (matching ``yfinance.Ticker``).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from ..models import Fundamentals

logger = logging.getLogger(__name__)


def _default_ticker_factory(symbol: str) -> Any:
    import yfinance as yf

    return yf.Ticker(symbol)


def _f(value: Any) -> Optional[float]:
    """Coerce to float, treating None/NaN/non-numeric as missing."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


class FundamentalsFetcher:
    def __init__(self, ticker_factory: Optional[Callable[[str], Any]] = None,
                 cache_ttl_seconds: float = 3600.0) -> None:
        self._factory = ticker_factory or _default_ticker_factory
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, Fundamentals]] = {}

    def fetch(self, ticker: str) -> Fundamentals:
        ticker = ticker.strip().upper()
        now = time.time()
        cached = self._cache.get(ticker)
        if cached and (now - cached[0]) < self._cache_ttl:
            return cached[1]

        try:
            info = self._get_info(ticker)
            fundamentals = self._parse(ticker, info)
        except Exception as exc:  # network/rate-limit/parse failures
            logger.warning("Fundamentals fetch failed for %s: %s", ticker, exc)
            fundamentals = Fundamentals(ticker=ticker, error=str(exc))

        self._cache[ticker] = (now, fundamentals)
        return fundamentals

    def fetch_many(self, tickers: list[str]) -> dict[str, Fundamentals]:
        return {t: self.fetch(t) for t in tickers}

    def _get_info(self, ticker: str) -> dict:
        obj = self._factory(ticker)
        info = getattr(obj, "info", None)
        if not isinstance(info, dict):
            raise ValueError("no info mapping returned")
        return info

    @staticmethod
    def _parse(ticker: str, info: dict) -> Fundamentals:
        # yfinance reports debtToEquity as a percentage (e.g. 150.0 == 1.5x).
        d2e_raw = _f(info.get("debtToEquity"))
        debt_to_equity = (d2e_raw / 100.0) if d2e_raw is not None else None

        earnings_growth = _f(info.get("earningsGrowth"))
        if earnings_growth is None:
            earnings_growth = _f(info.get("earningsQuarterlyGrowth"))

        peg = _f(info.get("pegRatio"))
        if peg is None:
            peg = _f(info.get("trailingPegRatio"))

        current_price = _f(info.get("currentPrice"))
        if current_price is None:
            current_price = _f(info.get("regularMarketPrice"))

        return Fundamentals(
            ticker=ticker,
            revenue_growth=_f(info.get("revenueGrowth")),
            earnings_growth=earnings_growth,
            profit_margin=_f(info.get("profitMargins")),
            roe=_f(info.get("returnOnEquity")),
            debt_to_equity=debt_to_equity,
            free_cash_flow=_f(info.get("freeCashflow")),
            trailing_pe=_f(info.get("trailingPE")),
            peg_ratio=peg,
            price_to_book=_f(info.get("priceToBook")),
            market_cap=_f(info.get("marketCap")),
            current_price=current_price,
            target_mean_price=_f(info.get("targetMeanPrice")),
            sector=info.get("sector"),
            name=info.get("shortName") or info.get("longName"),
        )
