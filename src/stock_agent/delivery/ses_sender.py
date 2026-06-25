"""Email delivery via AWS SES.

Sends the multipart (HTML + plain text) report using SES ``send_email``. The
boto3 SES client is injected so tests run without AWS. Also provides a small
``send_error_alert`` helper used by the orchestrator to notify on failures.

SES sandbox note
----------------
A brand-new SES account is in the *sandbox*: you can only send to and from
verified identities, and there's a low daily cap. For a personal "email myself"
agent that's fine — just verify both your sender and recipient address. See
docs/SES_SETUP.md. No need to request production access.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import boto3

from ..config import Config
from ..report.builder import Report

logger = logging.getLogger(__name__)


class SesEmailSender:
    def __init__(self, config: Config, ses_client: Optional[Any] = None) -> None:
        self.config = config
        self._client = ses_client or boto3.client("ses", region_name=config.aws_region)

    def send_report(self, report: Report) -> str:
        """Send the report email. Returns the SES MessageId."""
        if not self.config.sender_email:
            raise ValueError("SENDER_EMAIL is not configured")
        if not self.config.recipient_emails:
            raise ValueError("RECIPIENT_EMAILS is not configured")

        resp = self._client.send_email(
            Source=self.config.sender_email,
            Destination={"ToAddresses": list(self.config.recipient_emails)},
            Message={
                "Subject": {"Data": report.subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": report.text_body, "Charset": "UTF-8"},
                    "Html": {"Data": report.html_body, "Charset": "UTF-8"},
                },
            },
        )
        message_id = resp.get("MessageId", "")
        logger.info("Sent report to %s (MessageId=%s)",
                    self.config.recipient_emails, message_id)
        return message_id

    def send_error_alert(self, subject: str, body: str) -> Optional[str]:
        """Best-effort failure notification. Never raises."""
        target = self.config.error_email or (
            self.config.recipient_emails[0] if self.config.recipient_emails else ""
        )
        if not self.config.sender_email or not target:
            logger.error("Cannot send error alert: sender/recipient not configured")
            return None
        try:
            resp = self._client.send_email(
                Source=self.config.sender_email,
                Destination={"ToAddresses": [target]},
                Message={
                    "Subject": {"Data": subject[:200], "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
                },
            )
            return resp.get("MessageId", "")
        except Exception as exc:  # alerting must never mask the original error
            logger.error("Failed to send error alert: %s", exc)
            return None
