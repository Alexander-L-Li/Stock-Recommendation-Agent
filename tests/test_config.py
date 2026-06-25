from stock_agent.config import Config

import pytest


def test_defaults_are_valid():
    cfg = Config()
    cfg.validate()  # should not raise
    assert cfg.fundamentals_weight == 0.70
    assert cfg.sentiment_weight == 0.30
    assert "stocks" in cfg.subreddits
    assert cfg.table_name == "stock-agent"


def test_weights_must_sum_to_one():
    cfg = Config(fundamentals_weight=0.8, sentiment_weight=0.3)
    with pytest.raises(ValueError):
        cfg.validate()


def test_hype_gate_range_validated():
    cfg = Config(hype_gate_min_fundamentals=150)
    with pytest.raises(ValueError):
        cfg.validate()


def test_from_env_overrides(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "my-table")
    monkeypatch.setenv("SUBREDDITS", "stocks, wallstreetbets ,investing")
    monkeypatch.setenv("FUNDAMENTALS_WEIGHT", "0.6")
    monkeypatch.setenv("SENTIMENT_WEIGHT", "0.4")
    monkeypatch.setenv("RECIPIENT_EMAILS", "me@example.com")
    cfg = Config.from_env()
    assert cfg.table_name == "my-table"
    assert cfg.subreddits == ["stocks", "wallstreetbets", "investing"]
    assert cfg.fundamentals_weight == 0.6
    assert cfg.sentiment_weight == 0.4
    # error_email defaults to first recipient
    assert cfg.error_email == "me@example.com"


def test_from_env_bad_weights_raise(monkeypatch):
    monkeypatch.setenv("FUNDAMENTALS_WEIGHT", "0.9")
    monkeypatch.setenv("SENTIMENT_WEIGHT", "0.9")
    with pytest.raises(ValueError):
        Config.from_env()
