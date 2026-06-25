"""DynamoDB single-table store.

Table ``stock-agent`` layout:

  Watchlist items
    PK = "WATCHLIST"          SK = "TICKER#<symbol>"
  Run history picks
    PK = "RUN#<date>"         SK = "PICK#<rank:03d>#<symbol>"
  Run metadata summary
    PK = "RUN#<date>"         SK = "META"
  Per-ticker history index (for trend queries across runs)
    PK = "TICKER#<symbol>"    SK = "RUN#<date>"

The per-ticker index duplicates a small score summary so a ticker's history can
be queried directly without scanning every run.
"""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

from ..models import ScoredCandidate


def _to_dynamo(value: Any) -> Any:
    """Recursively convert floats to Decimal (DynamoDB has no float type).

    Non-finite floats (NaN / +/-Inf) are dropped to ``None`` because DynamoDB
    rejects them; this guards the point-in-time snapshot against the occasional
    bad value from an upstream data source.
    """
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        # str() round-trips cleanly into Decimal without binary float noise.
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dynamo(v) for v in value]
    return value


def _from_dynamo(value: Any) -> Any:
    """Recursively convert Decimal back to int/float for application use."""
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


WATCHLIST_PK = "WATCHLIST"
RUNS_INDEX_PK = "RUNS"


def _feature_snapshot(c: ScoredCandidate) -> dict:
    """Capture the point-in-time raw feature vector used to score a pick.

    Persisting the as-of inputs (not just the output scores) makes every
    recommendation auditable and is a prerequisite for an honest, look-ahead-free
    backtest: the backtest reads ``entry_price`` recorded here rather than
    re-fetching (potentially restated) fundamentals later.
    """
    snap: dict[str, Any] = {}
    f = c.fundamentals
    if f is not None:
        fundamentals = {
            "revenue_growth": f.revenue_growth,
            "earnings_growth": f.earnings_growth,
            "profit_margin": f.profit_margin,
            "roe": f.roe,
            "debt_to_equity": f.debt_to_equity,
            "free_cash_flow": f.free_cash_flow,
            "trailing_pe": f.trailing_pe,
            "peg_ratio": f.peg_ratio,
            "price_to_book": f.price_to_book,
            "market_cap": f.market_cap,
            "current_price": f.current_price,
            "target_mean_price": f.target_mean_price,
        }
        # Drop missing metrics to keep the item compact.
        snap["fundamentals"] = {k: v for k, v in fundamentals.items()
                                if v is not None}
    s = c.sentiment
    if s is not None:
        snap["sentiment"] = {
            "mention_count": s.mention_count,
            "avg_sentiment": s.avg_sentiment,
            "news_count": s.news_count,
            "avg_news_sentiment": s.avg_news_sentiment,
        }
    pf = getattr(c, "factors", None)
    if pf is not None and getattr(pf, "error", None) is None:
        factors = {
            "momentum": pf.momentum,
            "volatility": pf.volatility,
            "beta": pf.beta,
            "max_drawdown": pf.max_drawdown,
            "avg_dollar_volume": pf.avg_dollar_volume,
        }
        present = {k: v for k, v in factors.items() if v is not None}
        if present:
            snap["factors"] = present
    return snap


class Store:
    """Thin wrapper over a DynamoDB table resource."""

    def __init__(self, table_name: str, region: str = "us-east-1",
                 dynamodb_resource: Any = None) -> None:
        self.table_name = table_name
        self._resource = dynamodb_resource or boto3.resource(
            "dynamodb", region_name=region
        )
        self.table = self._resource.Table(table_name)

    # ----- table management (used by tests and one-time setup) -----
    @classmethod
    def create_table(cls, table_name: str, region: str = "us-east-1",
                     dynamodb_resource: Any = None) -> "Store":
        resource = dynamodb_resource or boto3.resource(
            "dynamodb", region_name=region
        )
        resource.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        return cls(table_name, region=region, dynamodb_resource=resource)

    # ----- watchlist -----
    def add_to_watchlist(self, ticker: str, note: str = "") -> None:
        ticker = ticker.strip().upper()
        if not ticker:
            raise ValueError("ticker must be non-empty")
        self.table.put_item(
            Item={
                "PK": WATCHLIST_PK,
                "SK": f"TICKER#{ticker}",
                "ticker": ticker,
                "note": note,
            }
        )

    def remove_from_watchlist(self, ticker: str) -> None:
        ticker = ticker.strip().upper()
        self.table.delete_item(
            Key={"PK": WATCHLIST_PK, "SK": f"TICKER#{ticker}"}
        )

    def list_watchlist(self) -> list[str]:
        resp = self.table.query(
            KeyConditionExpression=Key("PK").eq(WATCHLIST_PK)
            & Key("SK").begins_with("TICKER#")
        )
        return sorted(item["ticker"] for item in resp.get("Items", []))

    # ----- run history -----
    def save_run(self, run_date: str, candidates: list[ScoredCandidate],
                 meta: Optional[dict] = None) -> None:
        """Persist a full run: a META summary item plus one item per pick,
        and a per-ticker index entry for trend queries."""
        with self.table.batch_writer() as batch:
            summary = {
                "run_date": run_date,
                "pick_count": len(candidates),
                "tickers": [c.ticker for c in candidates],
            }
            if meta:
                summary.update(meta)
            batch.put_item(
                Item=_to_dynamo(
                    {"PK": f"RUN#{run_date}", "SK": "META", **summary}
                )
            )
            for c in candidates:
                f = c.fundamentals
                pick_item = {
                    "PK": f"RUN#{run_date}",
                    "SK": f"PICK#{c.rank:03d}#{c.ticker}",
                    "run_date": run_date,
                    "ticker": c.ticker,
                    "rank": c.rank,
                    "final_score": c.final_score,
                    "fundamentals_score": c.fundamentals_score,
                    "sentiment_score": c.sentiment_score,
                    "gated": c.gated,
                    "rationale": c.rationale,
                    "supporting_signals": c.supporting_signals,
                    "risks": c.risks,
                    # Point-in-time anchors for backtesting (#2).
                    "sector": (f.sector if f else None),
                    "market_cap": (f.market_cap if f else None),
                    "snapshot": _feature_snapshot(c),
                }
                # entry_price is the as-of price the forward return is measured
                # from; only store it when known so the backtest can rely on it.
                if f is not None and f.current_price is not None:
                    pick_item["entry_price"] = f.current_price
                batch.put_item(Item=_to_dynamo(pick_item))
                # Per-ticker history index (compact).
                batch.put_item(
                    Item=_to_dynamo(
                        {
                            "PK": f"TICKER#{c.ticker}",
                            "SK": f"RUN#{run_date}",
                            "run_date": run_date,
                            "ticker": c.ticker,
                            "rank": c.rank,
                            "final_score": c.final_score,
                            "fundamentals_score": c.fundamentals_score,
                            "sentiment_score": c.sentiment_score,
                        }
                    )
                )
            # Run-date index so the backtest can enumerate runs without scanning.
            batch.put_item(
                Item=_to_dynamo(
                    {
                        "PK": RUNS_INDEX_PK,
                        "SK": f"RUN#{run_date}",
                        "run_date": run_date,
                        "pick_count": len(candidates),
                    }
                )
            )

    def get_run(self, run_date: str) -> dict:
        """Return {"meta": {...}, "picks": [...]} for a given run date."""
        resp = self.table.query(
            KeyConditionExpression=Key("PK").eq(f"RUN#{run_date}")
        )
        items = [_from_dynamo(i) for i in resp.get("Items", [])]
        meta = {}
        picks = []
        for item in items:
            if item.get("SK") == "META":
                meta = item
            elif str(item.get("SK", "")).startswith("PICK#"):
                picks.append(item)
        picks.sort(key=lambda p: p.get("rank", 9999))
        return {"meta": meta, "picks": picks}

    def get_ticker_history(self, ticker: str, limit: int = 30) -> list[dict]:
        """Return a ticker's score history across runs, most recent first."""
        ticker = ticker.strip().upper()
        resp = self.table.query(
            KeyConditionExpression=Key("PK").eq(f"TICKER#{ticker}")
            & Key("SK").begins_with("RUN#"),
            ScanIndexForward=False,
            Limit=limit,
        )
        return [_from_dynamo(i) for i in resp.get("Items", [])]

    def list_run_dates(self, limit: int = 400) -> list[str]:
        """Return stored run dates, most recent first.

        Backed by the ``RUNS`` index written in :meth:`save_run`. Only runs
        saved after the point-in-time snapshot upgrade appear here.
        """
        resp = self.table.query(
            KeyConditionExpression=Key("PK").eq(RUNS_INDEX_PK)
            & Key("SK").begins_with("RUN#"),
            ScanIndexForward=False,
            Limit=limit,
        )
        return [i["run_date"] for i in resp.get("Items", [])]
