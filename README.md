# Daily Long-Term Stock Recommendation Agent

An autonomous, **free** agent that runs once daily, discovers candidate stocks
from **StockTwits**, **Reddit**, and business news, scores them primarily on
**financial fundamentals** (70%) with **social/news sentiment** as a secondary
tilt (30%), and emails a ranked, explainable report. Long-term horizon, no
trading, personal use.

> Signals and reasoning to aid your own research — **not financial advice**.

## Social data sources

Social signals come from a pluggable set of collectors that **aggregate into one
combined sentiment signal**:

- **StockTwits** — finance-focused, no API key required (public v2 endpoints).
  Provides trending-symbol discovery, per-symbol message streams, and native
  Bullish/Bearish labels. This is the default social source.
- **Reddit** — investing subreddits via `praw`. **Disabled by default** because
  Reddit now gates all API access behind an approval request (Responsible
  Builder Policy). Once you have approved credentials, set `ENABLE_REDDIT=true`
  plus `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` — no code changes; StockTwits
  and Reddit then aggregate together.

## How it works

```
EventBridge (daily cron)
        │
        ▼
   Lambda: orchestrator
        ├── StockTwits collector ────┐
        ├── Reddit collector (praw) ──┤  (combined "social" signal)
        ├── News collector (RSS)     ─┤→ ticker extraction (+ watchlist)
        │                             │        │
        │                             │        ▼
        │                             └→ sentiment (VADER)   fundamentals (yfinance)
        │                                        │                  │
        │                                        ▼                  ▼
        │                                  scoring engine (70/30 + hype gate)
        │                                        │
        │                                        ▼
        │                              report builder (HTML + text)
        │                                   │             │
        │                                   ▼             ▼
        │                              SES email     DynamoDB history
        ▼
   CloudWatch error alarm → SNS  (+ best-effort error email)
```

### Scoring model (fundamentals-dominant)

- **Fundamentals (70%)** — weighted composite of revenue growth, earnings
  growth, profit margin, ROE, debt/equity (inverted), free cash flow, and
  valuation sanity (PEG preferred, else trailing P/E). Each metric is normalized
  to 0–100; weights renormalize over whichever metrics are present.
- **Sentiment + news (30%)** — count-weighted Reddit + news polarity mapped to
  0–100, blended with a log-scaled mention-volume boost. No mentions ⇒ neutral 50
  (no tilt).
- **Hype gate** — if a stock's fundamentals score is below a threshold, sentiment
  may only *drag it down*, never *lift it*. This protects the long-term thesis
  from meme spikes.
- **Data-quality gate** — candidates with a fetch error or too few available
  fundamental metrics are excluded from the ranked picks (and listed separately).

`final = 0.7 × fundamentals + 0.3 × sentiment` (gates applied first).

## Project layout

```
src/stock_agent/
  config.py              # env-driven config + validation
  models.py              # dataclasses shared across stages
  collectors/            # stocktwits_collector.py, reddit_collector.py, news_collector.py
  extraction/            # ticker_extractor.py (+ tickers_data.py allow/deny lists)
  sentiment/             # base.py (interface + aggregator), vader.py
  fundamentals/          # yfinance_fetcher.py (caching, missing-data handling)
  scoring/               # engine.py (70/30 + hype gate + explainability)
  report/                # builder.py (HTML + plain text)
  delivery/              # ses_sender.py
  storage/               # dynamo.py (single-table: watchlist + history)
  orchestrator.py        # wires the pipeline; build_default() for production
  lambda_handler.py      # EventBridge entry point + error alerting
  cli.py                 # local CLI + watchlist management
infra/template.yaml      # SAM template (Lambda + EventBridge + DynamoDB + alarm/SNS)
deploy/build_lambda.sh   # builds the Lambda zip (manylinux wheels for numpy/pandas)
deploy/deploy_aws.sh     # one-shot deploy via AWS CLI (no SAM/Docker required)
docs/SES_SETUP.md        # SES sandbox setup (email yourself)
docs/DEPLOYMENT.md       # full deploy walkthrough
tests/                   # 84 tests, fully mocked (no network/AWS)
```

## Quick start (local)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Run the test suite (no network/AWS needed)
pytest

# Configure (see docs/SES_SETUP.md to verify SES addresses first)
export AWS_REGION=us-east-1
export TABLE_NAME=stock-agent
export SENDER_EMAIL=you@example.com
export RECIPIENT_EMAILS=you@example.com
export REDDIT_CLIENT_ID=...        # https://www.reddit.com/prefs/apps (script app)
export REDDIT_CLIENT_SECRET=...

# Create the DynamoDB table once (or via SAM, below)
aws dynamodb create-table --table-name stock-agent \
  --attribute-definitions AttributeName=PK,AttributeType=S AttributeName=SK,AttributeType=S \
  --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST --region us-east-1

# Manage your watchlist
python -m stock_agent.cli watchlist add NVDA
python -m stock_agent.cli watchlist list

# Preview a report without emailing/persisting (writes report.html)
python -m stock_agent.cli preview --open

# Full run (emails + writes history)
python -m stock_agent.cli run

# Inspect history
python -m stock_agent.cli history NVDA
python -m stock_agent.cli show 2026-06-25
```

## Configuration

All configuration is environment-driven (`Config.from_env()`), so the same code
runs locally and in Lambda. Key variables:

| Variable | Default | Meaning |
|---|---|---|
| `TABLE_NAME` | `stock-agent` | DynamoDB table |
| `AWS_REGION` | `us-east-1` | Region for DynamoDB + SES |
| `SENDER_EMAIL` / `RECIPIENT_EMAILS` | — | SES-verified addresses (comma-separated recipients) |
| `ERROR_EMAIL` | first recipient | Failure-alert address |
| `ENABLE_STOCKTWITS` | true | Toggle the StockTwits collector |
| `STOCKTWITS_SYMBOL_LIMIT` | 20 | Max symbols (trending+watchlist) fetched per run |
| `ENABLE_REDDIT` | true | Toggle the Reddit collector (needs approved creds) |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | — | Reddit script-app OAuth creds |
| `SUBREDDITS` | stocks,investing,ValueInvesting,StockMarket | Discovery subreddits |
| `NEWS_FEEDS` | Yahoo Finance, MarketWatch | RSS feeds |
| `LOOKBACK_HOURS` | 24 | Discovery window |
| `FUNDAMENTALS_WEIGHT` / `SENTIMENT_WEIGHT` | 0.70 / 0.30 | Must sum to 1.0 |
| `HYPE_GATE_MIN_FUNDAMENTALS` | 40 | Min fundamentals score to allow sentiment lift |
| `MIN_FUNDAMENTAL_METRICS` | 3 | Data-quality gate |
| `MAX_CANDIDATES` | 40 | Universe cap per run |
| `TOP_N` | 10 | Picks in the report |

## Cost

A once-daily job stays within AWS free tier: Lambda (1 invoke/day), DynamoDB
(PAY_PER_REQUEST, tiny), EventBridge (free scheduled rule), SES sandbox (free,
email-to-self). Data sources (yfinance, Reddit API, RSS) are free.

## Deployment

See **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** for the full walkthrough and
**[docs/SES_SETUP.md](docs/SES_SETUP.md)** for SES sandbox verification.

If you don't have the SAM CLI or Docker, `deploy/deploy_aws.sh` provisions
everything (DynamoDB, IAM role, Lambda, EventBridge schedule, SNS alarm) using
only the AWS CLI:

```bash
SENDER_EMAIL=you@example.com RECIPIENT_EMAILS=you@example.com \
  ALARM_EMAIL=you@example.com bash deploy/deploy_aws.sh
```

## Extensibility

- **Social sources** — collectors are pluggable; StockTwits and Reddit both emit
  records consumed by the same discovery + sentiment aggregation, so adding a
  source (or enabling Reddit) requires no scoring/report changes.
- **Sentiment backend** — `VaderAnalyzer` implements the `SentimentAnalyzer`
  protocol (`score(text) -> float`); StockTwits' native Bullish/Bearish labels
  are blended in. FinBERT can drop in behind the same interface (container image
  if it exceeds the Lambda zip limit). VADER is the guaranteed-free MVP.
- **Watchlist** — managed via the CLI (`watchlist add/remove/list`) against
  DynamoDB; no code edits needed.
