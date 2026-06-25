"""Pytest fixtures shared across the test suite."""
import os

import pytest


@pytest.fixture
def aws_credentials(monkeypatch):
    """Mocked AWS credentials so moto never touches real AWS."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def store(aws_credentials):
    """A Store backed by an in-memory moto DynamoDB table."""
    from moto import mock_aws

    from stock_agent.storage.dynamo import Store

    with mock_aws():
        yield Store.create_table("stock-agent-test", region="us-east-1")
