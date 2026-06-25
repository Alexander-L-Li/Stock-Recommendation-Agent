"""AWS Lambda entry point.

EventBridge invokes ``lambda_handler`` once daily. It builds config from
environment variables, runs the orchestrator, and on any unhandled failure
sends a best-effort error email before re-raising (so the failure also surfaces
in CloudWatch / Lambda error metrics, which can drive a CloudWatch alarm + SNS).
"""
from __future__ import annotations

import logging
import os
import traceback

logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def lambda_handler(event=None, context=None):  # noqa: ANN001
    from .config import Config
    from .orchestrator import Orchestrator

    # A weekly EventBridge rule sends {"mode": "backtest"} to run attribution
    # instead of the daily recommendation pipeline (one function, two schedules).
    if isinstance(event, dict) and event.get("mode") == "backtest":
        return backtest_handler(event, context)

    config = Config.from_env()
    try:
        result = Orchestrator.build_default(config).run()
        logger.info("Run complete: %s picks, %s excluded, emailed=%s",
                    result.ranked_count, result.excluded_count, result.emailed)
        return {
            "statusCode": 200,
            "run_date": result.run_date,
            "ranked": result.ranked_count,
            "excluded": result.excluded_count,
            "emailed": result.emailed,
            "messageId": result.message_id,
        }
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("Run failed: %s\n%s", exc, tb)
        _alert(config, exc, tb)
        raise  # surface to CloudWatch for alarms


def backtest_handler(event=None, context=None):  # noqa: ANN001
    """Periodic (e.g. weekly) performance backtest of past recommendations.

    Reads the recommendation history, measures realized forward returns vs the
    benchmark, and emails an attribution report. Wire this to its own EventBridge
    schedule (see deploy/deploy_aws.sh ENABLE_BACKTEST_SCHEDULE).
    """
    from .analysis.backtest import BacktestEngine, YFinancePriceProvider
    from .config import Config
    from .delivery.ses_sender import SesEmailSender
    from .report.backtest_report import format_backtest_report
    from .storage.dynamo import Store

    config = Config.from_env()
    try:
        store = Store(config.table_name, region=config.aws_region)
        result = BacktestEngine(store, YFinancePriceProvider()).run()
        report = format_backtest_report(result)
        message_id = SesEmailSender(config).send_report(report)
        logger.info("Backtest complete: %s observations across %s runs",
                    result.n_observations, result.n_runs)
        return {
            "statusCode": 200,
            "as_of": result.as_of,
            "runs": result.n_runs,
            "observations": result.n_observations,
            "pending": result.pending,
            "emailed": True,
            "messageId": message_id,
        }
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("Backtest failed: %s\n%s", exc, tb)
        _alert(config, exc, tb)
        raise


def _alert(config, exc, tb) -> None:
    """Send a best-effort failure email. Never raises."""
    try:
        from .delivery.ses_sender import SesEmailSender

        sender = SesEmailSender(config)
        sender.send_error_alert(
            subject=f"[Stock Agent] Daily run FAILED: {type(exc).__name__}",
            body=f"The daily stock agent run failed.\n\n{exc}\n\n{tb}",
        )
    except Exception as alert_exc:  # pragma: no cover - defensive
        logger.error("Could not send failure alert: %s", alert_exc)
