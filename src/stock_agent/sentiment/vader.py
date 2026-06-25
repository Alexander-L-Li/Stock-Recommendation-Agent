"""VADER sentiment analyzer (MVP backend).

VADER is lightweight, dependency-free at runtime (pure Python), and tuned for
short social-media style text — a good fit for Reddit and headlines, and it fits
comfortably inside a Lambda zip. Implements the ``SentimentAnalyzer`` protocol.
"""
from __future__ import annotations


class VaderAnalyzer:
    def __init__(self) -> None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        self._analyzer = SentimentIntensityAnalyzer()

    def score(self, text: str) -> float:
        """Return VADER compound score in [-1, 1]."""
        if not text:
            return 0.0
        return float(self._analyzer.polarity_scores(text)["compound"])
