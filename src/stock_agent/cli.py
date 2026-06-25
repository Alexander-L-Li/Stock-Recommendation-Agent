"""Command-line interface for local use and watchlist management.

Commands:
  run            Run the full pipeline now (writes history; emails unless --no-email)
  preview        Run but skip email and persistence; write report to a file
  watchlist add/remove/list
  history TICKER Show a ticker's score history
  show DATE      Show a stored run's picks
  backtest       Attribute past picks' forward returns vs SPY (optionally email)
  send-test      Send a tiny test email to verify SES setup
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from .config import Config


def _store(config: Config):
    from .storage.dynamo import Store

    return Store(config.table_name, region=config.aws_region)


def cmd_run(args, config: Config) -> int:
    from .orchestrator import Orchestrator

    orch = Orchestrator.build_default(config)
    result = orch.run(send_email=not args.no_email)
    print(f"Run {result.run_date}: {result.ranked_count} picks, "
          f"{result.excluded_count} excluded, emailed={result.emailed}")
    print(result.report.text_body)
    return 0


def cmd_preview(args, config: Config) -> int:
    from .orchestrator import Orchestrator

    orch = Orchestrator.build_default(config)
    result = orch.run(send_email=False, persist=False)
    out = Path(args.out)
    out.write_text(result.report.html_body, encoding="utf-8")
    print(f"Wrote {out.resolve()}")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_watchlist(args, config: Config) -> int:
    store = _store(config)
    if args.action == "add":
        store.add_to_watchlist(args.ticker, note=args.note or "")
        print(f"Added {args.ticker.upper()}")
    elif args.action == "remove":
        store.remove_from_watchlist(args.ticker)
        print(f"Removed {args.ticker.upper()}")
    elif args.action == "list":
        tickers = store.list_watchlist()
        print("\n".join(tickers) if tickers else "(watchlist empty)")
    return 0


def cmd_history(args, config: Config) -> int:
    store = _store(config)
    rows = store.get_ticker_history(args.ticker, limit=args.limit)
    if not rows:
        print(f"No history for {args.ticker.upper()}")
        return 0
    print(f"{'DATE':<12}{'RANK':>5}{'FINAL':>8}{'FUND':>8}{'SENT':>8}")
    for r in rows:
        print(f"{r['run_date']:<12}{r.get('rank',''):>5}"
              f"{r.get('final_score',''):>8}{r.get('fundamentals_score',''):>8}"
              f"{r.get('sentiment_score',''):>8}")
    return 0


def cmd_show(args, config: Config) -> int:
    store = _store(config)
    run = store.get_run(args.date)
    if not run["picks"]:
        print(f"No run stored for {args.date}")
        return 0
    print(f"Run {args.date}: {run['meta'].get('pick_count', 0)} picks")
    for p in run["picks"]:
        print(f"  #{p['rank']:>2} {p['ticker']:<6} {p['final_score']:>6} "
              f"{p.get('rationale','')}")
    return 0


def cmd_backtest(args, config: Config) -> int:
    from .analysis.backtest import BacktestEngine, YFinancePriceProvider
    from .report.backtest_report import format_backtest_report

    store = _store(config)
    engine = BacktestEngine(store, YFinancePriceProvider(),
                            horizons=tuple(args.horizons))
    result = engine.run()
    report = format_backtest_report(result)
    print(report.text_body)
    if args.email:
        from .delivery.ses_sender import SesEmailSender

        msg_id = SesEmailSender(config).send_report(report)
        print(f"\nEmailed backtest report (MessageId={msg_id})")
    return 0


def cmd_send_test(args, config: Config) -> int:
    from .delivery.ses_sender import SesEmailSender
    from .report.builder import Report

    sender = SesEmailSender(config)
    report = Report(
        subject="[Stock Agent] SES test email",
        html_body="<html><body><h2>Stock Agent</h2>"
                  "<p>SES is configured correctly.</p></body></html>",
        text_body="Stock Agent: SES is configured correctly.",
    )
    msg_id = sender.send_report(report)
    print(f"Sent test email (MessageId={msg_id}) to "
          f"{', '.join(config.recipient_emails)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stock-agent",
                                description="Daily stock recommendation agent")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the full pipeline now")
    run.add_argument("--no-email", action="store_true",
                     help="Skip sending the email")
    run.set_defaults(func=cmd_run)

    prev = sub.add_parser("preview", help="Run without email/persistence")
    prev.add_argument("--out", default="report.html", help="Output HTML file")
    prev.add_argument("--open", action="store_true", help="Open in browser")
    prev.set_defaults(func=cmd_preview)

    wl = sub.add_parser("watchlist", help="Manage the watchlist")
    wl.add_argument("action", choices=["add", "remove", "list"])
    wl.add_argument("ticker", nargs="?", default="")
    wl.add_argument("--note", default="")
    wl.set_defaults(func=cmd_watchlist)

    hist = sub.add_parser("history", help="Show a ticker's score history")
    hist.add_argument("ticker")
    hist.add_argument("--limit", type=int, default=30)
    hist.set_defaults(func=cmd_history)

    show = sub.add_parser("show", help="Show a stored run by date (YYYY-MM-DD)")
    show.add_argument("date")
    show.set_defaults(func=cmd_show)

    test = sub.add_parser("send-test", help="Send a test email via SES")
    test.set_defaults(func=cmd_send_test)

    bt = sub.add_parser("backtest",
                        help="Backtest past recommendations vs SPY")
    bt.add_argument("--horizons", type=int, nargs="+",
                    default=[30, 90, 180, 365],
                    help="Forward-return horizons in days")
    bt.add_argument("--email", action="store_true",
                    help="Email the backtest report via SES")
    bt.set_defaults(func=cmd_backtest)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "watchlist" and args.action in ("add", "remove") \
            and not args.ticker:
        print("error: ticker required for add/remove", file=sys.stderr)
        return 2
    config = Config.from_env()
    return args.func(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
