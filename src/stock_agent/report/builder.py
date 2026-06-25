"""Report builder.

Turns a ranked list of :class:`ScoredCandidate` into an emailable report with
both an HTML and a plain-text body. The report is explainability-first: every
pick shows its final score, the fundamentals/sentiment split, key metrics, a
written rationale, supporting signals, and risk flags.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date
from typing import Optional

from ..models import Fundamentals, ScoredCandidate


@dataclass
class Report:
    subject: str
    html_body: str
    text_body: str


def _pct(x: Optional[float]) -> str:
    return f"{x * 100:.1f}%" if x is not None else "—"


def _num(x: Optional[float], fmt: str = "{:.2f}") -> str:
    return fmt.format(x) if x is not None else "—"


def _money(x: Optional[float]) -> str:
    if x is None:
        return "—"
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(x) >= div:
            return f"${x / div:.2f}{unit}"
    return f"${x:.0f}"


def _metric_rows(f: Fundamentals) -> list[tuple[str, str]]:
    return [
        ("Revenue growth", _pct(f.revenue_growth)),
        ("Earnings growth", _pct(f.earnings_growth)),
        ("Profit margin", _pct(f.profit_margin)),
        ("ROE", _pct(f.roe)),
        ("Debt/Equity", _num(f.debt_to_equity)),
        ("Free cash flow", _money(f.free_cash_flow)),
        ("Trailing P/E", _num(f.trailing_pe)),
        ("PEG", _num(f.peg_ratio)),
        ("Price", _num(f.current_price, "${:.2f}")),
        ("Analyst target", _num(f.target_mean_price, "${:.2f}")),
    ]


def _upside(f: Fundamentals) -> Optional[float]:
    if f.current_price and f.target_mean_price and f.current_price > 0:
        return f.target_mean_price / f.current_price - 1.0
    return None


def _headline_stats(f: Fundamentals) -> str:
    """A single compact, high-impact line: sector · price · cap · upside · valn."""
    bits: list[str] = []
    if f.sector:
        bits.append(f.sector)
    if f.current_price is not None:
        bits.append(f"${f.current_price:.2f}")
    if f.market_cap is not None:
        bits.append(f"{_money(f.market_cap)} cap")
    up = _upside(f)
    if up is not None:
        bits.append(f"{up * 100:+.0f}% to target")
    if f.peg_ratio is not None and f.peg_ratio > 0:
        bits.append(f"PEG {f.peg_ratio:.2f}")
    elif f.trailing_pe is not None and f.trailing_pe > 0:
        bits.append(f"P/E {f.trailing_pe:.1f}")
    return "  ·  ".join(bits)


def _risk_line(pf) -> str:
    """Compact price-risk strip: momentum · volatility · beta · drawdown · liq."""
    if pf is None or getattr(pf, "error", None) is not None:
        return ""
    bits: list[str] = []
    if pf.momentum is not None:
        bits.append(f"{pf.momentum * 100:+.0f}% 12-mo")
    if pf.volatility is not None:
        bits.append(f"{pf.volatility * 100:.0f}% vol")
    if pf.beta is not None:
        bits.append(f"β {pf.beta:.2f}")
    if pf.max_drawdown is not None:
        bits.append(f"{pf.max_drawdown * 100:.0f}% maxDD")
    if pf.avg_dollar_volume is not None:
        bits.append(f"{_money(pf.avg_dollar_volume)}/day")
    return "  ·  ".join(bits)


def _sentiment_line(s) -> str:
    """Compact social/news sentiment summary for a holding."""
    if s is None:
        return ""
    bits = [f"{s.mention_count} mention" + ("s" if s.mention_count != 1 else "")]
    if s.mention_count:
        bits.append(f"avg {s.avg_sentiment:+.2f}")
    if s.news_count:
        bits.append(f"{s.news_count} news ({s.avg_news_sentiment:+.2f})")
    return " · ".join(bits)


# Buy/hold/trim signal thresholds (transparent, not financial advice).
_SIGNAL_STRONG = 70.0
_SIGNAL_WEAK = 45.0
_SENT_POS = 55.0
_SENT_SOFT = 45.0
_MOM_POS = 0.15
_MOM_NEG = -0.15


def holding_signal(c: ScoredCandidate) -> tuple[str, str]:
    """A lightweight ADD / HOLD / TRIM / WATCH signal for a held position.

    Combines the blended score, sentiment, and 12-mo momentum into a single
    actionable label plus a short, explainable reason. Deliberately simple and
    rule-based — it surfaces *why* so the owner can make the call. Not advice.

    - ADD   — strong score with confirming sentiment/momentum (consider buying)
    - TRIM  — weak score, or deteriorating momentum *and* soft sentiment
    - HOLD  — stable; nothing actionable
    - WATCH — not enough fundamental data to judge; monitor news/sentiment
    """
    f = c.fundamentals
    if f is None or getattr(f, "error", None):
        return ("WATCH", "limited fundamental data — monitor news & sentiment")

    score = c.final_score
    sent = c.sentiment_score
    mom = None
    if c.factors is not None and getattr(c.factors, "error", None) is None:
        mom = c.factors.momentum

    pos_sent, soft_sent = sent >= _SENT_POS, sent < _SENT_SOFT
    pos_mom = mom is not None and mom >= _MOM_POS
    neg_mom = mom is not None and mom <= _MOM_NEG

    reasons: list[str] = []
    if score >= _SIGNAL_STRONG:
        reasons.append(f"strong score {score:.0f}")
    elif score < _SIGNAL_WEAK:
        reasons.append(f"weak score {score:.0f}")
    else:
        reasons.append(f"score {score:.0f}")
    if pos_sent:
        reasons.append("positive sentiment")
    elif soft_sent:
        reasons.append("soft sentiment")
    if pos_mom:
        reasons.append(f"+{mom * 100:.0f}% momentum")
    elif neg_mom:
        reasons.append(f"{mom * 100:.0f}% momentum")

    if score >= _SIGNAL_STRONG and (pos_sent or pos_mom) and not neg_mom:
        label = "ADD"
    elif score < _SIGNAL_WEAK or (neg_mom and soft_sent):
        label = "TRIM"
    else:
        label = "HOLD"
    return (label, ", ".join(reasons))


_SIGNAL_COLORS = {
    "ADD": ("#dcfce7", "#166534"),    # green
    "HOLD": ("#e5e7eb", "#374151"),   # gray
    "TRIM": ("#fee2e2", "#991b1b"),   # red
    "WATCH": ("#fef9c3", "#854d0e"),  # amber
}


class ReportBuilder:
    def __init__(self, top_n: int = 10) -> None:
        self.top_n = top_n

    def build(self, ranked: list[ScoredCandidate],
              run_date: Optional[str] = None,
              excluded: Optional[list[ScoredCandidate]] = None,
              stats: Optional[dict] = None,
              holdings: Optional[list[ScoredCandidate]] = None) -> Report:
        run_date = run_date or date.today().isoformat()
        picks = ranked[: self.top_n]
        holdings = holdings or []
        subject = self._subject(picks, run_date)
        text_body = self._text(picks, run_date, excluded or [], stats or {},
                               holdings)
        html_body = self._html(picks, run_date, excluded or [], stats or {},
                               holdings)
        return Report(subject=subject, html_body=html_body, text_body=text_body)

    def _subject(self, picks: list[ScoredCandidate], run_date: str) -> str:
        if not picks:
            return f"Stock Agent — {run_date}: no qualifying picks"
        top = picks[0]
        names = ", ".join(c.ticker for c in picks[:3])
        return (f"Stock Agent — {run_date}: {len(picks)} picks "
                f"(top {top.ticker} {top.final_score:.0f}) — {names}")

    # ----------------- plain text -----------------
    def _holdings_text(self, holdings: list[ScoredCandidate]) -> list[str]:
        lines: list[str] = []
        lines.append("YOUR HOLDINGS")
        lines.append("-" * 60)
        lines.append("Daily tracker for stocks you own — signal is a rule-based "
                     "cue, not advice.")
        for c in holdings:
            label, reason = holding_signal(c)
            name = (f"  ({c.fundamentals.name})"
                    if c.fundamentals and c.fundamentals.name else "")
            lines.append(f"[{label}]  {c.ticker}{name}")
            f = c.fundamentals
            price = (f"${f.current_price:.2f}"
                     if f and f.current_price is not None else "—")
            lines.append(f"   Score {c.final_score:.0f}/100  "
                         f"[fundamentals {c.fundamentals_score:.0f} | "
                         f"sentiment {c.sentiment_score:.0f}"
                         + ("  | HYPE-GATED" if c.gated else "") + "]"
                         f"   Price {price}")
            lines.append(f"   Signal: {label} — {reason}")
            sline = _sentiment_line(c.sentiment)
            if sline:
                lines.append(f"   Sentiment: {sline}")
            risk = _risk_line(c.factors)
            if risk:
                lines.append(f"   Risk: {risk}")
            if c.news:
                lines.append("   Recent news:")
                for n in c.news:
                    src = f" ({n.source})" if n.source else ""
                    lines.append(f"     - {n.title}{src}")
            lines.append("")
        return lines

    # ----------------- plain text -----------------
    def _text(self, picks: list[ScoredCandidate], run_date: str,
              excluded: list[ScoredCandidate], stats: dict,
              holdings: Optional[list[ScoredCandidate]] = None) -> str:
        lines: list[str] = []
        lines.append(f"DAILY STOCK RECOMMENDATION REPORT — {run_date}")
        lines.append("=" * 60)
        lines.append("Fundamentals-dominant (70%) + sentiment/news (30%), "
                     "hype-gated.")
        if stats:
            social = stats.get('social_posts', stats.get('reddit_posts', '?'))
            lines.append(
                f"Universe: {stats.get('candidates', '?')} candidates "
                f"({social} social posts — "
                f"{stats.get('reddit_posts', 0)} reddit, "
                f"{stats.get('stocktwits_posts', 0)} stocktwits; "
                f"{stats.get('news_articles', '?')} news items)."
            )
        lines.append("")

        if holdings:
            lines.extend(self._holdings_text(holdings))

        if not picks:
            lines.append("No candidates cleared the data-quality gate today.")
        else:
            lines.append("TODAY'S TOP PICKS")
            lines.append("-" * 60)
        for c in picks:
            lines.append(f"#{c.rank}  {c.ticker}"
                         + (f"  ({c.fundamentals.name})"
                            if c.fundamentals and c.fundamentals.name else ""))
            lines.append(f"   Score {c.final_score:.0f}/100  "
                         f"[fundamentals {c.fundamentals_score:.0f} | "
                         f"sentiment {c.sentiment_score:.0f}"
                         + ("  | HYPE-GATED" if c.gated else "") + "]")
            if c.fundamentals:
                headline = _headline_stats(c.fundamentals)
                if headline:
                    lines.append(f"   {headline}")
            risk = _risk_line(c.factors)
            if risk:
                lines.append(f"   Risk: {risk}")
            lines.append(f"   {c.rationale}")
            if c.supporting_signals:
                lines.append("   + " + "; ".join(c.supporting_signals))
            if c.risks:
                lines.append("   ! " + "; ".join(c.risks))
            if c.fundamentals:
                metrics = "  ".join(f"{k}: {v}" for k, v in _metric_rows(c.fundamentals))
                lines.append("   " + metrics)
            if c.news:
                lines.append("   Recent news:")
                for n in c.news:
                    src = f" ({n.source})" if n.source else ""
                    lines.append(f"     - {n.title}{src}")
            lines.append("")

        if excluded:
            lines.append("-" * 60)
            lines.append("Excluded (insufficient data / fetch errors): "
                         + ", ".join(c.ticker for c in excluded))
        lines.append("")
        lines.append("Signals and reasoning to aid your own research — "
                     "not financial advice.")
        return "\n".join(lines)

    # ----------------- HTML -----------------
    def _html(self, picks: list[ScoredCandidate], run_date: str,
              excluded: list[ScoredCandidate], stats: dict,
              holdings: Optional[list[ScoredCandidate]] = None) -> str:
        def esc(s) -> str:
            return html.escape(str(s))

        parts: list[str] = []
        parts.append(
            "<html><body style=\"font-family:-apple-system,Segoe UI,Arial,"
            "sans-serif;color:#1a1a1a;max-width:760px;margin:auto;\">"
        )
        parts.append(f"<h1 style=\"margin-bottom:0;\">Daily Stock Recommendations</h1>")
        parts.append(f"<p style=\"color:#666;margin-top:4px;\">{esc(run_date)} · "
                     "Fundamentals-dominant (70%) + sentiment/news (30%), "
                     "hype-gated</p>")
        if stats:
            social = stats.get('social_posts', stats.get('reddit_posts', '?'))
            parts.append(
                f"<p style=\"color:#888;font-size:13px;\">Universe: "
                f"{esc(stats.get('candidates', '?'))} candidates · "
                f"{esc(social)} social posts "
                f"({esc(stats.get('reddit_posts', 0))} reddit, "
                f"{esc(stats.get('stocktwits_posts', 0))} stocktwits) · "
                f"{esc(stats.get('news_articles', '?'))} news items</p>"
            )

        if holdings:
            parts.append(self._holdings_html(holdings))

        if not picks:
            parts.append("<p><em>No candidates cleared the data-quality gate "
                         "today.</em></p>")
        else:
            parts.append("<h2 style=\"margin:24px 0 4px;border-bottom:2px solid "
                         "#e5e7eb;padding-bottom:4px;\">Today's top picks</h2>")

        for c in picks:
            name = (f" — {esc(c.fundamentals.name)}"
                    if c.fundamentals and c.fundamentals.name else "")
            gate_badge = (
                "<span style=\"background:#fde68a;color:#92400e;padding:2px 6px;"
                "border-radius:4px;font-size:12px;margin-left:8px;\">HYPE-GATED</span>"
                if c.gated else "")
            parts.append("<div style=\"border:1px solid #e5e7eb;border-radius:8px;"
                         "padding:16px;margin:12px 0;\">")
            parts.append(
                f"<h2 style=\"margin:0;\">#{c.rank} {esc(c.ticker)}{name}"
                f"{gate_badge}</h2>"
            )
            parts.append(self._score_bar(c))
            if c.fundamentals:
                headline = _headline_stats(c.fundamentals)
                if headline:
                    parts.append(
                        "<p style=\"margin:2px 0 8px;color:#374151;font-size:13px;"
                        f"font-weight:600;\">{esc(headline)}</p>"
                    )
            risk = _risk_line(c.factors)
            if risk:
                parts.append(
                    "<p style=\"margin:0 0 8px;color:#6b7280;font-size:12px;\">"
                    f"Risk &amp; momentum: {esc(risk)}</p>"
                )
            parts.append(f"<p style=\"margin:8px 0;\">{esc(c.rationale)}</p>")

            if c.supporting_signals:
                items = "".join(f"<li>{esc(s)}</li>" for s in c.supporting_signals)
                parts.append("<p style=\"margin:4px 0;color:#15803d;\"><strong>"
                             f"Supporting signals</strong></p><ul>{items}</ul>")
            if c.risks:
                items = "".join(f"<li>{esc(r)}</li>" for r in c.risks)
                parts.append("<p style=\"margin:4px 0;color:#b91c1c;\"><strong>"
                             f"Risks</strong></p><ul>{items}</ul>")
            if c.fundamentals:
                parts.append(self._metric_table(c.fundamentals))
            if c.news:
                parts.append(self._news_block(c.news))
            parts.append("</div>")

        if excluded:
            names = ", ".join(esc(c.ticker) for c in excluded)
            parts.append(f"<p style=\"color:#888;font-size:13px;\"><strong>Excluded"
                         f"</strong> (insufficient data / fetch errors): {names}</p>")

        parts.append("<hr><p style=\"color:#999;font-size:12px;\">Signals and "
                     "reasoning to aid your own research — not financial advice.</p>")
        parts.append("</body></html>")
        return "".join(parts)

    @staticmethod
    def _score_bar(c: ScoredCandidate) -> str:
        f_pct = max(0, min(100, c.fundamentals_score))
        s_pct = max(0, min(100, c.sentiment_score))
        return (
            f"<p style=\"margin:6px 0;font-size:15px;\"><strong>Score "
            f"{c.final_score:.0f}/100</strong> "
            f"<span style=\"color:#666;\">(fundamentals {f_pct:.0f} · "
            f"sentiment {s_pct:.0f})</span></p>"
        )

    @staticmethod
    def _holdings_html(holdings: list[ScoredCandidate]) -> str:
        def esc(s) -> str:
            return html.escape(str(s))

        parts: list[str] = [
            "<h2 style=\"margin:20px 0 2px;\">Your holdings</h2>",
            "<p style=\"color:#888;font-size:12px;margin:0 0 8px;\">Daily tracker "
            "for stocks you own — the signal is a rule-based cue, not advice.</p>",
        ]
        for c in holdings:
            label, reason = holding_signal(c)
            bg, fg = _SIGNAL_COLORS.get(label, _SIGNAL_COLORS["HOLD"])
            f = c.fundamentals
            name = (f" — {esc(f.name)}" if f and f.name else "")
            price = (f"${f.current_price:.2f}"
                     if f and f.current_price is not None else "—")
            badge = (f"<span style=\"background:{bg};color:{fg};padding:2px 8px;"
                     f"border-radius:4px;font-size:12px;font-weight:700;"
                     f"margin-left:8px;\">{label}</span>")
            parts.append(
                "<div style=\"border:1px solid #e5e7eb;border-left:4px solid "
                f"{fg};border-radius:8px;padding:12px 16px;margin:10px 0;"
                "background:#fafafa;\">"
            )
            parts.append(f"<h3 style=\"margin:0;\">{esc(c.ticker)}{name}{badge}</h3>")
            parts.append(
                "<p style=\"margin:6px 0;font-size:14px;\"><strong>Score "
                f"{c.final_score:.0f}/100</strong> "
                f"<span style=\"color:#666;\">(fundamentals "
                f"{c.fundamentals_score:.0f} · sentiment {c.sentiment_score:.0f})"
                f"</span> · <span style=\"color:#374151;\">{esc(price)}</span></p>"
            )
            parts.append(
                f"<p style=\"margin:4px 0;color:{fg};font-size:13px;\">"
                f"<strong>{label}</strong> — {esc(reason)}</p>"
            )
            sline = _sentiment_line(c.sentiment)
            if sline:
                parts.append("<p style=\"margin:2px 0;color:#6b7280;font-size:12px;"
                             f"\">Sentiment: {esc(sline)}</p>")
            risk = _risk_line(c.factors)
            if risk:
                parts.append("<p style=\"margin:2px 0;color:#6b7280;font-size:12px;"
                             f"\">Risk &amp; momentum: {esc(risk)}</p>")
            if c.news:
                parts.append(ReportBuilder._news_block(c.news))
            parts.append("</div>")
        return "".join(parts)

    @staticmethod
    def _news_block(news: list) -> str:
        items = []
        for n in news:
            title = html.escape(n.title)
            src = (f" <span style=\"color:#9ca3af;\">· {html.escape(n.source)}"
                   "</span>" if n.source else "")
            if n.url:
                title = (f"<a href=\"{html.escape(n.url)}\" "
                         f"style=\"color:#1d4ed8;text-decoration:none;\">{title}</a>")
            items.append(f"<li style=\"margin:2px 0;\">{title}{src}</li>")
        return ("<p style=\"margin:8px 0 2px;color:#374151;\"><strong>Recent news"
                "</strong></p><ul style=\"font-size:13px;margin-top:2px;\">"
                + "".join(items) + "</ul>")

    @staticmethod
    def _metric_table(f: Fundamentals) -> str:
        cells = "".join(
            f"<td style=\"padding:4px 10px 4px 0;color:#555;\">{html.escape(k)}</td>"
            f"<td style=\"padding:4px 16px 4px 0;font-weight:600;\">"
            f"{html.escape(v)}</td>"
            + ("</tr><tr>" if (i % 2 == 1) else "")
            for i, (k, v) in enumerate(_metric_rows(f))
        )
        return ("<table style=\"font-size:13px;border-collapse:collapse;margin-top:"
                f"8px;\"><tr>{cells}</tr></table>")
