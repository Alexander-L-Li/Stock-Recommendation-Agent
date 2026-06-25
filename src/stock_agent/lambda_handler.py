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
