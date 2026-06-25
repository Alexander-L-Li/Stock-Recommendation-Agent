"""Scoring engine.

Produces a ranked list of :class:`ScoredCandidate` from fundamentals + sentiment.

Model (fundamentals-dominant 70/30):

  fundamentals_score (0-100)
      Weighted blend of normalized sub-metrics: revenue growth, earnings growth,
      profit margin, ROE, debt/equity (inverted), free cash flow, and valuation
      sanity (PEG preferred, else trailing P/E). Sub-weights are renormalized
      over whichever metrics are present so missing data doesn't unfairly zero a
      candidate (data-quality is handled by a separate gate).

  sentiment_score (0-100)
      Polarity of Reddit + news sentiment (count-weighted) mapped from [-1,1] to
      [0,100], blended with a log-scaled mention-volume boost. No mentions at all
      => neutral 50 (no tilt).

  final_score
      0.7 * fundamentals + 0.3 * sentiment, with two gates applied first:

      * Data-quality gate: candidates with a fetch error or fewer than
        ``min_fundamental_metrics`` available metrics are excluded from picks.
      * Hype gate: if fundamentals_score is below the configured threshold,
        sentiment may only drag the score down, never lift it — protecting the
        long-term thesis from meme spikes.
"""
from __future__ import annotations

import math
from typing import Optional

from ..config import Config
from ..models import Fundamentals, ScoredCandidate, SentimentResult

# Sub-metric weights within the fundamentals composite (sum to 1.0).
_FUNDAMENTAL_WEIGHTS = {
    "revenue_growth": 0.18,
    "earnings_growth": 0.18,
    "profit_margin": 0.15,
    "roe": 0.15,
    "debt_to_equity": 0.12,
    "free_cash_flow": 0.10,
    "valuation": 0.12,
}

# Mention volume that maps to a full volume boost (diminishing returns via log).
_VOLUME_SATURATION = 25


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _scale(value: float, lo: float, hi: float) -> float:
    """Linear map: ``lo`` -> 0, ``hi`` -> 100, clamped to [0,100].
    Works for descending scales too (when ``lo`` > ``hi``)."""
    if hi == lo:
        return 50.0
    return _clamp((value - lo) / (hi - lo) * 100.0)


class ScoringEngine:
    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()

    # ---------------- fundamentals ----------------
    def score_fundamentals(self, f: Fundamentals) -> tuple[float, dict[str, float]]:
        """Return (composite 0-100, per-metric sub-scores)."""
        subs: dict[str, float] = {}

        if f.revenue_growth is not None:
            # -10% -> 0, +30% -> 100
            subs["revenue_growth"] = _scale(f.revenue_growth, -0.10, 0.30)
        if f.earnings_growth is not None:
            subs["earnings_growth"] = _scale(f.earnings_growth, -0.10, 0.30)
        if f.profit_margin is not None:
            # 0% -> 0, 25% -> 100
            subs["profit_margin"] = _scale(f.profit_margin, 0.0, 0.25)
        if f.roe is not None:
            # 0% -> 0, 30% -> 100
            subs["roe"] = _scale(f.roe, 0.0, 0.30)
        if f.debt_to_equity is not None:
            # inverted: 0x -> 100, 2.5x -> 0
            subs["debt_to_equity"] = _scale(f.debt_to_equity, 2.5, 0.0)
        if f.free_cash_flow is not None:
            # positive FCF rewarded; negative penalized
            subs["free_cash_flow"] = 85.0 if f.free_cash_flow > 0 else 10.0
        valuation = self._valuation_subscore(f)
        if valuation is not None:
            subs["valuation"] = valuation

        if not subs:
            return 0.0, subs

        # Renormalize weights over present metrics.
        total_w = sum(_FUNDAMENTAL_WEIGHTS[k] for k in subs)
        composite = sum(subs[k] * _FUNDAMENTAL_WEIGHTS[k] for k in subs) / total_w
        return _clamp(composite), subs

    @staticmethod
    def _valuation_subscore(f: Fundamentals) -> Optional[float]:
        """Valuation sanity. Prefer PEG (growth-adjusted), else trailing P/E.
        Reasonable valuations score high; rich or nonsensical ones score low."""
        if f.peg_ratio is not None and f.peg_ratio > 0:
            # PEG 0.5 -> ~100, 1.0 -> ~75, 2.0 -> ~25, 3+ -> 0
            return _scale(f.peg_ratio, 3.0, 0.5)
        if f.trailing_pe is not None:
            if f.trailing_pe <= 0:
                return 15.0  # negative earnings: weak valuation signal
            # P/E 10 -> 100, 40 -> 0
            return _scale(f.trailing_pe, 40.0, 10.0)
        return None

    # ---------------- sentiment ----------------
    def score_sentiment(self, s: Optional[SentimentResult]) -> float:
        if s is None:
            return 50.0
        total = s.mention_count + s.news_count
        if total == 0:
            return 50.0  # no information => neutral, no tilt

        # Count-weighted polarity across reddit + news.
        weighted = (
            s.avg_sentiment * s.mention_count
            + s.avg_news_sentiment * s.news_count
        ) / total
        polarity = (weighted + 1.0) / 2.0 * 100.0  # [-1,1] -> [0,100]

        volume = math.log1p(total) / math.log1p(_VOLUME_SATURATION) * 100.0
        volume = _clamp(volume)

        return _clamp(0.75 * polarity + 0.25 * volume)

    # ---------------- composite + ranking ----------------
    def score_candidate(self, ticker: str, fundamentals: Fundamentals,
                        sentiment: Optional[SentimentResult]) -> ScoredCandidate:
        f_score, subs = self.score_fundamentals(fundamentals)
        s_score = self.score_sentiment(sentiment)

        base = (self.config.fundamentals_weight * f_score
                + self.config.sentiment_weight * s_score)

        gated = f_score < self.config.hype_gate_min_fundamentals
        if gated:
            # Sentiment may only drag down, never lift a weak-fundamental name.
            final = min(base, f_score)
        else:
            final = base

        cand = ScoredCandidate(
            ticker=ticker,
            final_score=round(_clamp(final), 2),
            fundamentals_score=round(f_score, 2),
            sentiment_score=round(s_score, 2),
            gated=gated,
            fundamentals=fundamentals,
            sentiment=sentiment,
        )
        self._explain(cand, subs)
        return cand

    def rank(
        self,
        fundamentals: dict[str, Fundamentals],
        sentiment: dict[str, SentimentResult],
    ) -> tuple[list[ScoredCandidate], list[ScoredCandidate]]:
        """Score all candidates.

        Returns ``(ranked, excluded)`` where ``excluded`` holds candidates that
        failed the data-quality gate (fetch error or too few metrics).
        """
        ranked: list[ScoredCandidate] = []
        excluded: list[ScoredCandidate] = []

        for ticker, f in fundamentals.items():
            s = sentiment.get(ticker)
            cand = self.score_candidate(ticker, f, s)
            insufficient = (
                f.error is not None
                or f.available_count() < self.config.min_fundamental_metrics
            )
            if insufficient:
                if f.error:
                    cand.risks.append(f"Fundamentals unavailable: {f.error}")
                else:
                    cand.risks.append(
                        f"Insufficient fundamental data "
                        f"({f.available_count()} metrics)"
                    )
                excluded.append(cand)
            else:
                ranked.append(cand)

        ranked.sort(key=lambda c: c.final_score, reverse=True)
        for i, cand in enumerate(ranked, start=1):
            cand.rank = i
        return ranked, excluded

    # ---------------- explainability ----------------
    def _explain(self, cand: ScoredCandidate, subs: dict[str, float]) -> None:
        f = cand.fundamentals
        s = cand.sentiment
        signals = cand.supporting_signals
        risks = cand.risks

        def pct(x):
            return f"{x * 100:.0f}%"

        # Strengths
        if f.revenue_growth is not None and f.revenue_growth >= 0.10:
            signals.append(f"Revenue growth {pct(f.revenue_growth)}")
        if f.earnings_growth is not None and f.earnings_growth >= 0.10:
            signals.append(f"Earnings growth {pct(f.earnings_growth)}")
        if f.profit_margin is not None and f.profit_margin >= 0.15:
            signals.append(f"Healthy margin {pct(f.profit_margin)}")
        if f.roe is not None and f.roe >= 0.15:
            signals.append(f"Strong ROE {pct(f.roe)}")
        if f.free_cash_flow is not None and f.free_cash_flow > 0:
            signals.append("Positive free cash flow")
        if f.peg_ratio is not None and 0 < f.peg_ratio <= 1.2:
            signals.append(f"Attractive PEG {f.peg_ratio:.2f}")
        elif f.trailing_pe is not None and 0 < f.trailing_pe <= 20:
            signals.append(f"Reasonable P/E {f.trailing_pe:.1f}")

        # Risks
        if f.revenue_growth is not None and f.revenue_growth < 0:
            risks.append(f"Declining revenue ({pct(f.revenue_growth)})")
        if f.earnings_growth is not None and f.earnings_growth < 0:
            risks.append(f"Declining earnings ({pct(f.earnings_growth)})")
        if f.debt_to_equity is not None and f.debt_to_equity > 1.5:
            risks.append(f"Elevated debt/equity ({f.debt_to_equity:.2f})")
        if f.free_cash_flow is not None and f.free_cash_flow <= 0:
            risks.append("Negative free cash flow")
        if f.trailing_pe is not None and f.trailing_pe > 40:
            risks.append(f"Rich valuation (P/E {f.trailing_pe:.1f})")
        if f.peg_ratio is not None and f.peg_ratio > 2.5:
            risks.append(f"High PEG ({f.peg_ratio:.2f})")

        # Sentiment signal / hype note
        if s is not None and (s.mention_count + s.news_count) > 0:
            avg = s.avg_sentiment
            tone = "positive" if avg > 0.15 else "negative" if avg < -0.15 else "mixed"
            signals.append(
                f"{tone.capitalize()} social/news buzz "
                f"({s.mention_count} reddit, {s.news_count} news mentions)"
            )
        if cand.gated:
            risks.append(
                "Hype gate applied: fundamentals below threshold, so social "
                "buzz was not allowed to lift the score"
            )

        # Upside vs analyst target
        if f.current_price and f.target_mean_price and f.current_price > 0:
            upside = (f.target_mean_price / f.current_price - 1.0)
            if upside >= 0.05:
                signals.append(f"Analyst target implies {pct(upside)} upside")
            elif upside <= -0.05:
                risks.append(f"Trading above analyst target ({pct(upside)})")

        cand.rationale = self._compose_rationale(cand)

    @staticmethod
    def _compose_rationale(cand: ScoredCandidate) -> str:
        name = cand.fundamentals.name or cand.ticker
        fs, ss = cand.fundamentals_score, cand.sentiment_score
        if fs >= 70:
            strength = "strong fundamentals"
        elif fs >= 50:
            strength = "solid fundamentals"
        elif fs >= 35:
            strength = "acceptable fundamentals"
        else:
            strength = "weak fundamentals"

        lead = (
            f"{name} ({cand.ticker}) scores {cand.final_score:.0f}/100 on "
            f"{strength} (fundamentals {fs:.0f}, sentiment {ss:.0f})."
        )
        if cand.supporting_signals:
            lead += " Supporting: " + "; ".join(cand.supporting_signals[:4]) + "."
        if cand.risks:
            lead += " Watch: " + "; ".join(cand.risks[:3]) + "."
        return lead
