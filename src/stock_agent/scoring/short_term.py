"""Short-term (1-3 month) score evaluator.

A separate, momentum/technical-driven scorer for tactical 1-3 month picks. The
long-term :class:`~stock_agent.scoring.engine.ScoringEngine` is fundamentals-
dominant (70/30) and rightly cautious on high-multiple momentum names. Over a
1-3 month horizon the return drivers are different, so this evaluator flips the
emphasis:

    Momentum (1m + 3m return) ........ 35%   core short-term alpha
    Sentiment / news flow ............ 25%   far more predictive short-term
    Technical posture (RSI/52w/vol) .. 15%   breakout vs overbought exhaustion
    Trend (price vs SMA50/SMA200) .... 15%   regime filter, don't fight the tape
    Earnings momentum (rev/earn) ..... 10%   PEAD-style tailwind, light quality

On top of the blend, three risk guards apply:

  * **Liquidity gate** -- thin average dollar volume caps the score (you can't
    exit a tactical position cleanly in an illiquid name).
  * **Falling-knife guard** -- a sharply negative 1-month return while below the
    50-day average is penalized; short-term, you don't catch falling knives.
  * **Volatility penalty** -- extreme realized volatility shaves the score for
    blow-up risk.

The technical inputs are computed from a daily price series with pure-Python
helpers (no numpy/pandas) so the whole thing is unit-testable without network or
heavy deps, exactly like ``fundamentals/price_factors.py``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from ..config import Config
from ..models import Fundamentals, SentimentResult

# Trading-day windows.
_M1 = 21      # ~1 month
_M3 = 63      # ~3 months
_M6 = 126     # ~6 months
_SMA_FAST = 50
_SMA_SLOW = 200
_YEAR = 252
_RSI_PERIOD = 14

# Blend weights (sum to 1.0). Renormalized over whichever components are present.
_WEIGHTS = {
    "momentum": 0.35,
    "sentiment": 0.25,
    "posture": 0.15,
    "trend": 0.15,
    "earnings": 0.10,
}

# Mention+news volume that maps to a full short-term buzz boost.
_VOLUME_SATURATION = 25


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _scale(value: float, lo: float, hi: float) -> float:
    """Linear map ``lo`` -> 0, ``hi`` -> 100, clamped. Supports descending."""
    if hi == lo:
        return 50.0
    return _clamp((value - lo) / (hi - lo) * 100.0)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _ret(closes: list[float], window: int) -> Optional[float]:
    """Total return over the last ``window`` trading days."""
    if len(closes) <= window:
        return None
    past = closes[-1 - window]
    if not past:
        return None
    return closes[-1] / past - 1.0


def _sma(closes: list[float], window: int) -> Optional[float]:
    if len(closes) < window:
        return None
    return _mean(closes[-window:])


def _rsi(closes: list[float], period: int = _RSI_PERIOD) -> Optional[float]:
    """Classic RSI over the last ``period`` daily changes (0-100)."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1]
              for i in range(len(closes) - period, len(closes))]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _annualized_vol(closes: list[float], window: int = _M1) -> Optional[float]:
    if len(closes) < window + 1:
        return None
    rets = [closes[i] / closes[i - 1] - 1.0
            for i in range(len(closes) - window, len(closes))
            if closes[i - 1]]
    if len(rets) < 2:
        return None
    m = _mean(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(_YEAR)


@dataclass
class TechnicalSignals:
    """Short-term technical posture derived from a daily price series."""

    ticker: str
    ret_1m: Optional[float] = None
    ret_3m: Optional[float] = None
    ret_6m: Optional[float] = None
    sma50: Optional[float] = None
    sma200: Optional[float] = None
    price: Optional[float] = None
    rsi14: Optional[float] = None
    pct_from_52w_high: Optional[float] = None   # <= 0 (0 == at the high)
    vol_20d: Optional[float] = None             # annualized realized vol
    volume_ratio: Optional[float] = None        # recent vs baseline volume
    avg_dollar_volume: Optional[float] = None
    error: Optional[str] = None

    @property
    def above_sma50(self) -> Optional[bool]:
        if self.price is None or self.sma50 is None:
            return None
        return self.price >= self.sma50

    @property
    def uptrend(self) -> Optional[bool]:
        if self.sma50 is None or self.sma200 is None:
            return None
        return self.sma50 >= self.sma200


def compute_technicals(
    ticker: str, closes: list[float],
    volumes: Optional[list[float]] = None,
) -> TechnicalSignals:
    """Compute :class:`TechnicalSignals` from oldest-first daily closes."""
    closes = [c for c in closes if c is not None and math.isfinite(c)]
    if len(closes) < _M1 + 2:
        return TechnicalSignals(ticker=ticker, error="insufficient history")

    price = closes[-1]
    high_52w = max(closes[-_YEAR:]) if len(closes) >= _M1 else max(closes)
    pct_from_high = (price / high_52w - 1.0) if high_52w else None

    volume_ratio = None
    avg_dollar_volume = None
    if volumes:
        vols = [v for v in volumes if v is not None and math.isfinite(v)]
        if len(vols) >= 10:
            recent = _mean(vols[-10:])
            baseline = _mean(vols[-60:]) if len(vols) >= 60 else _mean(vols)
            if baseline:
                volume_ratio = recent / baseline
        n = min(_M1, len(vols), len(closes))
        if n:
            avg_dollar_volume = _mean(
                [c * v for c, v in zip(closes[-n:], vols[-n:])])

    return TechnicalSignals(
        ticker=ticker,
        ret_1m=_ret(closes, _M1),
        ret_3m=_ret(closes, _M3),
        ret_6m=_ret(closes, _M6),
        sma50=_sma(closes, _SMA_FAST),
        sma200=_sma(closes, _SMA_SLOW),
        price=price,
        rsi14=_rsi(closes),
        pct_from_52w_high=pct_from_high,
        vol_20d=_annualized_vol(closes),
        volume_ratio=volume_ratio,
        avg_dollar_volume=avg_dollar_volume,
    )


@dataclass
class ShortTermScore:
    """Result of the short-term evaluator for one ticker."""

    ticker: str
    score: float                       # 0-100, after risk guards
    base_score: float                  # blend before risk guards
    signal: str                        # STRONG BUY | BUY | WATCH | AVOID
    subs: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    technicals: Optional[TechnicalSignals] = None
    name: Optional[str] = None         # company name for display (optional)


class ShortTermEvaluator:
    """Momentum/technical scorer for tactical 1-3 month picks.

    Independent of :class:`ScoringEngine` (different horizon, different model),
    but shares the same data inputs (:class:`Fundamentals`,
    :class:`SentimentResult`) plus :class:`TechnicalSignals`.
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()

    # ---------------- component sub-scores ----------------
    def _momentum_score(self, t: TechnicalSignals) -> Optional[float]:
        comps: list[tuple[float, float]] = []  # (score, weight)
        if t.ret_3m is not None:
            comps.append((_scale(t.ret_3m, -0.20, 0.40), 0.6))
        if t.ret_1m is not None:
            comps.append((_scale(t.ret_1m, -0.15, 0.25), 0.4))
        if not comps:
            return None
        wsum = sum(w for _, w in comps)
        return sum(s * w for s, w in comps) / wsum

    def _trend_score(self, t: TechnicalSignals) -> Optional[float]:
        above = t.above_sma50
        up = t.uptrend
        if above is None and up is None:
            return None
        score = 50.0
        if above is not None:
            score = 70.0 if above else 30.0
        if up is not None:
            score += 15.0 if up else -15.0
        return _clamp(score)

    def _posture_score(self, t: TechnicalSignals) -> Optional[float]:
        comps: list[float] = []
        # RSI: reward healthy momentum (~55-65), penalize overbought (>75) and
        # deep oversold (<30, falling-knife territory for a 1-3mo entry).
        if t.rsi14 is not None:
            r = t.rsi14
            if r >= 75:
                comps.append(_scale(r, 90, 70))      # overbought -> low
            elif r >= 45:
                comps.append(75.0)                   # constructive zone
            elif r >= 30:
                comps.append(50.0)                   # neutral/pullback
            else:
                comps.append(_scale(r, 10, 35))      # deep oversold -> low
        # 52-week-high proximity: closer to the high = stronger continuation.
        if t.pct_from_52w_high is not None:
            comps.append(_scale(t.pct_from_52w_high, -0.40, 0.0))
        # Volume confirmation: rising recent volume supports a move.
        if t.volume_ratio is not None:
            comps.append(_scale(t.volume_ratio, 0.7, 1.6))
        if not comps:
            return None
        return _mean(comps)

    def _sentiment_score(self, s: Optional[SentimentResult]) -> Optional[float]:
        if s is None:
            return None
        total = s.mention_count + s.news_count
        if total == 0:
            return None
        weighted = (
            s.avg_sentiment * s.mention_count
            + s.avg_news_sentiment * s.news_count
        ) / total
        polarity = (weighted + 1.0) / 2.0 * 100.0
        volume = math.log1p(total) / math.log1p(_VOLUME_SATURATION) * 100.0
        return _clamp(0.7 * polarity + 0.3 * _clamp(volume))

    def _earnings_score(self, f: Optional[Fundamentals]) -> Optional[float]:
        if f is None or f.error is not None:
            return None
        comps: list[float] = []
        if f.earnings_growth is not None:
            comps.append(_scale(f.earnings_growth, -0.20, 0.40))
        if f.revenue_growth is not None:
            comps.append(_scale(f.revenue_growth, -0.10, 0.30))
        if not comps:
            return None
        return _mean(comps)

    # ---------------- composite ----------------
    def score_candidate(
        self, ticker: str,
        technicals: TechnicalSignals,
        sentiment: Optional[SentimentResult] = None,
        fundamentals: Optional[Fundamentals] = None,
    ) -> ShortTermScore:
        subs: dict[str, float] = {}
        for name, val in (
            ("momentum", self._momentum_score(technicals)),
            ("sentiment", self._sentiment_score(sentiment)),
            ("posture", self._posture_score(technicals)),
            ("trend", self._trend_score(technicals)),
            ("earnings", self._earnings_score(fundamentals)),
        ):
            if val is not None:
                subs[name] = round(val, 2)

        reasons: list[str] = []
        risks: list[str] = []

        if technicals.error is not None or "momentum" not in subs:
            # Without price history there is no short-term thesis to stand on.
            return ShortTermScore(
                ticker=ticker, score=0.0, base_score=0.0, signal="AVOID",
                subs=subs,
                risks=[f"No price history ({technicals.error or 'unavailable'})"],
                technicals=technicals,
            )

        total_w = sum(_WEIGHTS[k] for k in subs)
        base = sum(subs[k] * _WEIGHTS[k] for k in subs) / total_w

        score, guard_reasons = self._apply_risk_guards(base, technicals)
        risks.extend(guard_reasons)
        self._explain(subs, technicals, sentiment, reasons, risks)

        signal = self._signal(score, technicals)
        return ShortTermScore(
            ticker=ticker,
            score=round(_clamp(score), 2),
            base_score=round(_clamp(base), 2),
            signal=signal,
            subs=subs,
            reasons=reasons,
            risks=risks,
            technicals=technicals,
        )

    def _apply_risk_guards(
        self, base: float, t: TechnicalSignals
    ) -> tuple[float, list[str]]:
        score = base
        risks: list[str] = []

        # Falling-knife guard: sharp 1m drop while below the 50-day.
        if (t.ret_1m is not None and t.ret_1m <= -0.10
                and t.above_sma50 is False):
            score -= 15.0
            risks.append(
                f"Falling knife: {t.ret_1m * 100:.0f}% 1m return below SMA50")

        # Volatility penalty for blow-up risk.
        if t.vol_20d is not None and t.vol_20d > 0.60:
            pen = _scale(t.vol_20d, 0.60, 1.20) / 100.0 * 12.0  # up to ~12 pts
            score -= pen
            risks.append(f"High volatility ({t.vol_20d * 100:.0f}% annualized)")

        # Liquidity gate: thin names can't be exited cleanly in 1-3 months.
        adv = t.avg_dollar_volume
        if adv is not None and adv < self.config.min_dollar_volume:
            score = min(score, 50.0)
            risks.append(f"Thin liquidity (~${adv / 1e6:.1f}M/day)")

        return _clamp(score), risks

    @staticmethod
    def _signal(score: float, t: TechnicalSignals) -> str:
        if score >= 75:
            return "STRONG BUY"
        if score >= 60:
            return "BUY"
        if score >= 45:
            return "WATCH"
        return "AVOID"

    @staticmethod
    def _explain(subs, t, sentiment, reasons, risks) -> None:
        if t.ret_3m is not None and t.ret_3m >= 0.10:
            reasons.append(f"+{t.ret_3m * 100:.0f}% 3-month momentum")
        if t.ret_1m is not None and t.ret_1m >= 0.05:
            reasons.append(f"+{t.ret_1m * 100:.0f}% past month")
        if t.uptrend:
            reasons.append("Uptrend (50-day above 200-day)")
        if t.above_sma50:
            reasons.append("Price above 50-day average")
        if t.pct_from_52w_high is not None and t.pct_from_52w_high >= -0.05:
            reasons.append("Near 52-week high (breakout zone)")
        if t.rsi14 is not None and t.rsi14 >= 75:
            risks.append(f"Overbought (RSI {t.rsi14:.0f})")
        if t.rsi14 is not None and t.rsi14 <= 30:
            risks.append(f"Oversold (RSI {t.rsi14:.0f})")
        if subs.get("sentiment", 50) >= 60:
            reasons.append("Positive sentiment/news flow")
        if t.ret_3m is not None and t.ret_3m <= -0.10:
            risks.append(f"{t.ret_3m * 100:.0f}% 3-month decline")


class TechnicalFetcher:
    """Fetches daily price history and computes :class:`TechnicalSignals`.

    Mirrors :class:`~stock_agent.fundamentals.price_factors.PriceFactorFetcher`:
    the price source is injected via ``series_factory`` (defaulting to the same
    yfinance-backed loader the long-term factor fetcher uses) so tests run with
    no network. A small in-process TTL cache avoids re-fetching within a run.
    """

    def __init__(
        self,
        series_factory=None,
        cache_ttl_seconds: float = 3600.0,
    ) -> None:
        if series_factory is None:
            from ..fundamentals.price_factors import _default_series_factory
            series_factory = _default_series_factory
        self._factory = series_factory
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, TechnicalSignals]] = {}

    def fetch(self, ticker: str) -> TechnicalSignals:
        import time
        ticker = ticker.strip().upper()
        now = time.time()
        cached = self._cache.get(ticker)
        if cached and (now - cached[0]) < self._cache_ttl:
            return cached[1]
        try:
            series = self._factory(ticker)
            if series is None or not getattr(series, "closes", None):
                signals = TechnicalSignals(ticker=ticker, error="no price data")
            else:
                signals = compute_technicals(
                    ticker, series.closes,
                    getattr(series, "volumes", None))
        except Exception as exc:  # network/parse failures are non-fatal
            signals = TechnicalSignals(ticker=ticker, error=str(exc))
        self._cache[ticker] = (now, signals)
        return signals

    def fetch_many(self, tickers: list[str]) -> dict[str, TechnicalSignals]:
        return {t: self.fetch(t) for t in tickers}
