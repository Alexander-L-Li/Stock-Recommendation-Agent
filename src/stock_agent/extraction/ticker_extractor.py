"""Extract candidate tickers from Reddit posts and news articles.

Strategy (precision-favoring):
  1. ``$CASHTAGS`` — always accepted (1-5 letters, optional ``.`` class share
     suffix like BRK.B). These are explicit ticker references.
  2. Bare ALL-CAPS tokens — accepted only if they appear in the known-ticker
     allowlist and are not in the stopword denylist. This drops false positives
     like "CEO", "USA", "FDA".
  3. Watchlist tickers are merged in unconditionally.

Returns mention counts so downstream discovery can rank/cap the universe and so
sentiment aggregation knows how many times each ticker was referenced.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Optional

from ..models import NewsArticle, RedditPost
from .tickers_data import KNOWN_TICKERS, STOPWORDS

# $AAPL or $BRK.B  (cashtag)
_CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5}(?:\.[A-Za-z]{1,2})?)\b")
# Bare uppercase token, 1-5 letters, optional .X suffix, not preceded by '$'
_ALLCAPS_RE = re.compile(r"(?<![\$\w])([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\b")


class TickerExtractor:
    def __init__(self, known_tickers: Optional[Iterable[str]] = None,
                 stopwords: Optional[Iterable[str]] = None) -> None:
        self.known = (
            frozenset(t.upper() for t in known_tickers)
            if known_tickers is not None else KNOWN_TICKERS
        )
        self.stopwords = (
            frozenset(s.upper() for s in stopwords)
            if stopwords is not None else STOPWORDS
        )

    def extract_from_text(self, text: str) -> set[str]:
        """Return the set of distinct tickers found in a single text blob."""
        if not text:
            return set()
        found: set[str] = set()
        for m in _CASHTAG_RE.finditer(text):
            sym = m.group(1).upper()
            if sym not in self.stopwords:
                found.add(sym)
        for m in _ALLCAPS_RE.finditer(text):
            sym = m.group(1).upper()
            if sym in self.stopwords:
                continue
            if sym in self.known:
                found.add(sym)
        return found

    def count_mentions(self, texts: Iterable[str]) -> Counter:
        """Count how many texts mention each ticker (one count per text max)."""
        counter: Counter = Counter()
        for text in texts:
            for sym in self.extract_from_text(text):
                counter[sym] += 1
        return counter

    def discover(
        self,
        reddit_posts: Iterable[RedditPost],
        news_articles: Iterable[NewsArticle],
        watchlist: Optional[Iterable[str]] = None,
        min_mentions: int = 1,
        max_candidates: Optional[int] = None,
    ) -> tuple[list[str], Counter]:
        """Merge discovery sources + watchlist into a candidate ticker list.

        Returns ``(candidates, mention_counter)`` where ``candidates`` is ordered
        by descending mention count (watchlist tickers always included even with
        zero mentions). ``mention_counter`` covers reddit+news mentions only.
        """
        texts = [p.text for p in reddit_posts] + [a.text for a in news_articles]
        counter = self.count_mentions(texts)

        # Discovery candidates meeting the mention threshold.
        discovered = {t for t, c in counter.items() if c >= min_mentions}

        watch = {t.strip().upper() for t in (watchlist or []) if t.strip()}
        all_candidates = discovered | watch

        # Order: by mention count desc, then alphabetically for stability.
        ordered = sorted(
            all_candidates, key=lambda t: (-counter.get(t, 0), t)
        )
        if max_candidates is not None:
            # Never drop watchlist tickers due to the cap.
            capped = ordered[:max_candidates]
            for t in watch:
                if t not in capped:
                    capped.append(t)
            ordered = capped
        return ordered, counter
