"""Formats a :class:`BacktestResult` into an emailable text + HTML report."""
from __future__ import annotations

import html
from typing import Optional

from ..analysis.backtest import BacktestResult, HorizonStats
from .builder import Report


def _pct(x: Optional[float]) -> str:
    return f"{x * 100:+.1f}%" if x is not None else "—"


def _ic(x: Optional[float]) -> str:
    return f"{x:+.2f}" if x is not None else "—"


def _horizon_label(days: int) -> str:
    return {30: "1M", 90: "3M", 180: "6M", 365: "1Y"}.get(days, f"{days}d")


def _verdict(result: BacktestResult) -> str:
    """A one-line, honest headline read of the track record."""
    if result.n_observations == 0:
        return ("Not enough matured history yet to evaluate — picks need to age "
                "past the shortest horizon before returns can be measured.")
    # Use the longest horizon with data as the headline.
    h = result.horizons[-1]
    ic_note = (f"rank IC {_ic(h.rank_ic)}" if h.rank_ic is not None
               else "rank IC n/a (too few picks)")
    direction = "beating" if h.mean_excess_return > 0 else "trailing"
    return (f"Over {_horizon_label(h.horizon_days)}, picks averaged "
            f"{_pct(h.mean_stock_return)} vs {_pct(h.mean_bench_return)} for "
            f"{result.benchmark} ({_pct(h.mean_excess_return)} excess, "
            f"{direction} the benchmark), hit rate {h.hit_rate * 100:.0f}%, "
            f"{ic_note}.")


def format_backtest_report(result: BacktestResult) -> Report:
    subject = (f"Stock Agent — backtest {result.as_of}: "
               f"{result.n_observations} obs across {result.n_runs} runs")
    return Report(
        subject=subject,
        text_body=_text(result),
        html_body=_html(result),
    )


def _text(r: BacktestResult) -> str:
    lines: list[str] = []
    lines.append(f"STOCK AGENT — PERFORMANCE BACKTEST (as of {r.as_of})")
    lines.append("=" * 64)
    lines.append(_verdict(r))
    lines.append("")
    lines.append(f"Runs evaluated: {r.n_runs} | picks with entry price: "
                 f"{r.n_picks} | matured observations: {r.n_observations} | "
                 f"pending: {r.pending}")
    if r.skipped_no_entry_price:
        lines.append(f"Note: {r.skipped_no_entry_price} older picks skipped "
                     "(saved before point-in-time snapshots were added).")
    lines.append("")

    if r.horizons:
        header = (f"{'Horizon':<8}{'N':>5}{'Stock':>9}{'Bench':>9}"
                  f"{'Excess':>9}{'Hit':>7}{'Win':>7}{'RankIC':>8}"
                  f"{'Top½':>9}{'Bot½':>9}")
        lines.append(header)
        lines.append("-" * len(header))
        for h in r.horizons:
            lines.append(
                f"{_horizon_label(h.horizon_days):<8}{h.n:>5}"
                f"{_pct(h.mean_stock_return):>9}{_pct(h.mean_bench_return):>9}"
                f"{_pct(h.mean_excess_return):>9}{h.hit_rate * 100:>6.0f}%"
                f"{h.win_rate * 100:>6.0f}%{_ic(h.rank_ic):>8}"
                f"{_pct(h.top_half_excess):>9}{_pct(h.bottom_half_excess):>9}"
            )
        lines.append("")
        lines.append("Hit = % of picks beating the benchmark. RankIC = Spearman "
                     "corr(score, forward return); >0 means higher scores "
                     "predicted higher returns. Top½/Bot½ = mean excess return "
                     "of the higher- vs lower-scored half.")

    if r.best:
        b = r.best
        lines.append("")
        lines.append(f"Best:  {b.ticker} ({b.run_date}, "
                     f"{_horizon_label(b.horizon_days)}) {_pct(b.stock_return)} "
                     f"({_pct(b.excess_return)} excess)")
    if r.worst:
        w = r.worst
        lines.append(f"Worst: {w.ticker} ({w.run_date}, "
                     f"{_horizon_label(w.horizon_days)}) {_pct(w.stock_return)} "
                     f"({_pct(w.excess_return)} excess)")

    lines.append("")
    lines.append("Past performance does not guarantee future results — "
                 "not financial advice.")
    return "\n".join(lines)


def _html(r: BacktestResult) -> str:
    def esc(s) -> str:
        return html.escape(str(s))

    parts: list[str] = []
    parts.append("<html><body style=\"font-family:-apple-system,Segoe UI,Arial,"
                 "sans-serif;color:#1a1a1a;max-width:820px;margin:auto;\">")
    parts.append("<h1 style=\"margin-bottom:0;\">Performance Backtest</h1>")
    parts.append(f"<p style=\"color:#666;margin-top:4px;\">as of {esc(r.as_of)} · "
                 f"benchmark {esc(r.benchmark)}</p>")
    parts.append(f"<p style=\"font-size:15px;\"><strong>{esc(_verdict(r))}"
                 "</strong></p>")
    parts.append(
        f"<p style=\"color:#888;font-size:13px;\">Runs evaluated "
        f"{esc(r.n_runs)} · picks with entry price {esc(r.n_picks)} · "
        f"matured observations {esc(r.n_observations)} · pending {esc(r.pending)}"
        + (f" · {esc(r.skipped_no_entry_price)} older picks skipped"
           if r.skipped_no_entry_price else "")
        + "</p>"
    )

    if r.horizons:
        parts.append("<table style=\"border-collapse:collapse;font-size:13px;"
                     "width:100%;margin-top:8px;\">")
        cols = ["Horizon", "N", "Stock", "Bench", "Excess", "Hit", "Win",
                "Rank IC", "Top ½", "Bot ½"]
        parts.append("<tr style=\"background:#f3f4f6;text-align:right;\">"
                     + "".join(
                         f"<th style=\"padding:6px 10px;"
                         f"text-align:{'left' if c == 'Horizon' else 'right'};\">"
                         f"{esc(c)}</th>" for c in cols)
                     + "</tr>")
        for h in r.horizons:
            ex_color = "#15803d" if h.mean_excess_return > 0 else "#b91c1c"
            parts.append(
                "<tr style=\"border-top:1px solid #e5e7eb;text-align:right;\">"
                f"<td style=\"padding:6px 10px;text-align:left;font-weight:600;\">"
                f"{esc(_horizon_label(h.horizon_days))}</td>"
                f"<td style=\"padding:6px 10px;\">{esc(h.n)}</td>"
                f"<td style=\"padding:6px 10px;\">{esc(_pct(h.mean_stock_return))}</td>"
                f"<td style=\"padding:6px 10px;color:#666;\">"
                f"{esc(_pct(h.mean_bench_return))}</td>"
                f"<td style=\"padding:6px 10px;font-weight:600;color:{ex_color};\">"
                f"{esc(_pct(h.mean_excess_return))}</td>"
                f"<td style=\"padding:6px 10px;\">{h.hit_rate * 100:.0f}%</td>"
                f"<td style=\"padding:6px 10px;\">{h.win_rate * 100:.0f}%</td>"
                f"<td style=\"padding:6px 10px;font-weight:600;\">"
                f"{esc(_ic(h.rank_ic))}</td>"
                f"<td style=\"padding:6px 10px;\">{esc(_pct(h.top_half_excess))}</td>"
                f"<td style=\"padding:6px 10px;\">{esc(_pct(h.bottom_half_excess))}</td>"
                "</tr>"
            )
        parts.append("</table>")
        parts.append("<p style=\"color:#888;font-size:12px;\">Hit = % of picks "
                     "beating the benchmark. Rank IC = Spearman corr(score, "
                     "forward return); &gt;0 means higher scores predicted "
                     "higher returns. Top ½/Bot ½ = mean excess return of the "
                     "higher- vs lower-scored half of picks.</p>")

    if r.best or r.worst:
        parts.append("<p style=\"font-size:13px;\">")
        if r.best:
            b = r.best
            parts.append(f"<strong>Best:</strong> {esc(b.ticker)} "
                         f"({esc(b.run_date)}, {esc(_horizon_label(b.horizon_days))}) "
                         f"{esc(_pct(b.stock_return))} "
                         f"({esc(_pct(b.excess_return))} excess)<br>")
        if r.worst:
            w = r.worst
            parts.append(f"<strong>Worst:</strong> {esc(w.ticker)} "
                         f"({esc(w.run_date)}, {esc(_horizon_label(w.horizon_days))}) "
                         f"{esc(_pct(w.stock_return))} "
                         f"({esc(_pct(w.excess_return))} excess)")
        parts.append("</p>")

    parts.append("<hr><p style=\"color:#999;font-size:12px;\">Past performance "
                 "does not guarantee future results — not financial advice.</p>")
    parts.append("</body></html>")
    return "".join(parts)
