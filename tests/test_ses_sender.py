import pytest

from stock_agent.config import Config
from stock_agent.delivery.ses_sender import SesEmailSender
from stock_agent.report.builder import Report


class FakeSes:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def send_email(self, **kwargs):
        if self.fail:
            raise RuntimeError("MessageRejected: email not verified")
        self.calls.append(kwargs)
        return {"MessageId": "msg-123"}


def _config():
    return Config(sender_email="me@example.com",
                  recipient_emails=["me@example.com"])


def _report():
    return Report(subject="Test subject",
                  html_body="<html><body>hi</body></html>",
                  text_body="hi")


def test_send_report_payload_assembly():
    ses = FakeSes()
    sender = SesEmailSender(_config(), ses_client=ses)
    msg_id = sender.send_report(_report())

    assert msg_id == "msg-123"
    assert len(ses.calls) == 1
    call = ses.calls[0]
    assert call["Source"] == "me@example.com"
    assert call["Destination"]["ToAddresses"] == ["me@example.com"]
    assert call["Message"]["Subject"]["Data"] == "Test subject"
    assert call["Message"]["Body"]["Text"]["Data"] == "hi"
    assert "<html>" in call["Message"]["Body"]["Html"]["Data"]


def test_send_report_requires_sender():
    cfg = Config(sender_email="", recipient_emails=["me@example.com"])
    sender = SesEmailSender(cfg, ses_client=FakeSes())
    with pytest.raises(ValueError):
        sender.send_report(_report())


def test_send_report_requires_recipients():
    cfg = Config(sender_email="me@example.com", recipient_emails=[])
    sender = SesEmailSender(cfg, ses_client=FakeSes())
    with pytest.raises(ValueError):
        sender.send_report(_report())


def test_error_alert_sent():
    ses = FakeSes()
    sender = SesEmailSender(_config(), ses_client=ses)
    msg_id = sender.send_error_alert("Agent failed", "traceback here")
    assert msg_id == "msg-123"
    assert ses.calls[0]["Message"]["Subject"]["Data"] == "Agent failed"


def test_error_alert_never_raises():
    ses = FakeSes(fail=True)
    sender = SesEmailSender(_config(), ses_client=ses)
    # Should swallow the SES error and return None
    assert sender.send_error_alert("subj", "body") is None
