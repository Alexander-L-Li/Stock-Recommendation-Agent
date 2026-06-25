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


class ReportBuilder:
    def __init__(self, top_n: int = 10) -> None:
        self.top_n = top_n

    def build(self, ranked: list[ScoredCandidate],
              run_date: Optional[str] = None,
              excluded: Optional[list[ScoredCandidate]] = None,
              stats: Optional[dict] = None) -> Report:
        run_date = run_date or date.today().isoformat()
        picks = ranked[: self.top_n]
        subject = self._subject(picks, run_date)
        text_body = self._text(picks, run_date, excluded or [], stats or {})
        html_body = self._html(picks, run_date, excluded or [], stats or {})
        return Report(subject=subject, html_body=html_body, text_body=text_body)

    def _subject(self, picks: list[ScoredCandidate], run_date: str) -> str:
        if not picks:
            return f"Stock Agent — {run_date}: no qualifying picks"
        top = picks[0]
        names = ", ".join(c.ticker for c in picks[:3])
        return (f"Stock Agent — {run_date}: {len(picks)} picks "
                f"(top {top.ticker} {top.final_score:.0f}) — {names}")

    # ----------------- plain text -----------------
    def _text(self, picks: list[ScoredCandidate], run_date: str,
              excluded: list[ScoredCandidate], stats: dict) -> str:
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

        if not picks:
            lines.append("No candidates cleared the data-quality gate today.")
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
              excluded: list[ScoredCandidate], stats: dict) -> str:
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

        if not picks:
            parts.append("<p><em>No candidates cleared the data-quality gate "
                         "today.</em></p>")

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
