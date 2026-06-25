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

from decimal import Decimal
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

from ..models import ScoredCandidate


def _to_dynamo(value: Any) -> Any:
    """Recursively convert floats to Decimal (DynamoDB has no float type)."""
    if isinstance(value, float):
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
                }
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
