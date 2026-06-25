"""Lambda handler smoke tests (orchestrator + alerting patched)."""
import stock_agent.lambda_handler as lh
from stock_agent.orchestrator import RunResult
from stock_agent.report.builder import Report


def _env(monkeypatch):
    monkeypatch.setenv("SENDER_EMAIL", "me@example.com")
    monkeypatch.setenv("RECIPIENT_EMAILS", "me@example.com")
    monkeypatch.setenv("AWS_REGION", "us-east-1")


def test_handler_success(monkeypatch):
    _env(monkeypatch)

    fake_result = RunResult(
        run_date="2026-06-25",
        report=Report("s", "<html></html>", "t"),
        ranked_count=3, excluded_count=1, candidates=["AAPL"],
        message_id="msg-1", emailed=True,
    )

    class FakeOrch:
        @classmethod
        def build_default(cls, config, store=None):
            return cls()

        def run(self, *a, **k):
            return fake_result

    monkeypatch.setattr(lh, "Orchestrator", FakeOrch, raising=False)
    # Orchestrator is imported inside the function; patch the module attr it pulls.
    import stock_agent.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "Orchestrator", FakeOrch)

    out = lh.lambda_handler({}, None)
    assert out["statusCode"] == 200
    assert out["ranked"] == 3
    assert out["emailed"] is True
    assert out["messageId"] == "msg-1"


def test_handler_failure_alerts_and_reraises(monkeypatch):
    _env(monkeypatch)

    class BoomOrch:
        @classmethod
        def build_default(cls, config, store=None):
            return cls()

        def run(self, *a, **k):
            raise RuntimeError("collector exploded")

    import stock_agent.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "Orchestrator", BoomOrch)

    alerts = {}

    def fake_alert(config, exc, tb):
        alerts["called"] = (str(exc), tb)

    monkeypatch.setattr(lh, "_alert", fake_alert)

    try:
        lh.lambda_handler({}, None)
        assert False, "expected exception to propagate"
    except RuntimeError as exc:
        assert "collector exploded" in str(exc)

    assert "called" in alerts
    assert "collector exploded" in alerts["called"][0]
