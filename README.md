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
  collectors/            # stocktwits_collector.py, reddit_collector.py, news_collector.py, ticker_news.py
  extraction/            # ticker_extractor.py (+ tickers_data.py allow/deny lists)
  sentiment/             # base.py (interface + aggregator), vader.py
  fundamentals/          # yfinance_fetcher.py (caching, missing-data handling)
  scoring/               # engine.py (70/30 + hype gate + explainability)
  analysis/              # backtest.py (forward-return attribution vs SPY)
  report/                # builder.py (HTML + plain text), backtest_report.py
  delivery/              # ses_sender.py
  storage/               # dynamo.py (single-table: watchlist + history + snapshots)
  orchestrator.py        # wires the pipeline; build_default() for production
  lambda_handler.py      # daily entry point + weekly backtest_handler + error alerting
  cli.py                 # local CLI + watchlist management + backtest
infra/template.yaml      # SAM template (Lambda + EventBridge + DynamoDB + alarm/SNS)
deploy/build_lambda.sh   # builds the Lambda zip (manylinux wheels for numpy/pandas)
deploy/deploy_aws.sh     # one-shot deploy via AWS CLI (no SAM/Docker required)
docs/SES_SETUP.md        # SES sandbox setup (email yourself)
docs/DEPLOYMENT.md       # full deploy walkthrough
tests/                   # 105 tests, fully mocked (no network/AWS)
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

## Performance backtesting & track record

A recommendation engine is only credible if its picks are validated against
realized returns, so the agent keeps score on itself:

- **Point-in-time snapshots.** Every saved pick records the as-of `entry_price`,
  sector, market cap, and the full raw fundamentals + sentiment feature vector
  (`storage/dynamo._feature_snapshot`). This makes each recommendation auditable
  and lets the backtest measure forward returns from the price that was actually
  showing at recommendation time — no look-ahead bias.
- **Attribution engine** (`analysis/backtest.py`). Reads the history and, for
  horizons of 30/90/180/365 days, computes each pick's forward return, its
  **excess return vs SPY**, the **hit rate** (share beating the benchmark), and
  the **rank IC** — the Spearman correlation between the model's score and the
  realized return. A persistently positive IC is the evidence that the ranking
  carries genuine signal rather than noise.

Run it locally or on a schedule:

```bash
stock-agent backtest                 # text report to stdout
stock-agent backtest --email         # also email the attribution report
stock-agent backtest --horizons 30 90 180
```

The weekly Lambda entry point is `stock_agent.lambda_handler.backtest_handler`;
`deploy/deploy_aws.sh` provisions an optional weekly EventBridge schedule for it
when `ENABLE_BACKTEST_SCHEDULE=true`.

> The backtest only sees runs saved **after** point-in-time snapshots were added,
> and a pick must age past the shortest horizon before it can be scored — so the
> track record builds up over time.

## Roadmap (next credibility upgrades)

Quant-reviewed improvements, in priority order. These are deliberately *not* yet
implemented; the backtest above is the prerequisite that makes them measurable.

1. **Sector-relative, cross-sectional scoring (#3).** Replace the hand-tuned
   linear metric thresholds in `scoring/engine.py` (e.g. revenue growth
   `-10%→+30%`, P/E `40→10`, the binary FCF cliff) with **percentile/z-scores
   computed within each stock's sector**. This removes the magic numbers and
   stops comparing, say, a bank's ROE to a software firm's margin. `sector` is
   already fetched and now persisted, so the data is in place.
2. **Price-based risk & momentum factors (#4).** Add 6–12m momentum, realized
   volatility, beta, max drawdown, and a liquidity (ADV) filter from yfinance
   `.history()` — the same price source the backtest uses. Risk-adjust the final
   score so a "long-term" recommendation can be defended on a risk basis, not
   just growth/valuation.
3. **Decouple the universe from social discovery (#5).** Today the candidate set
   *is* whatever StockTwits/Reddit/news mention, which biases toward already-hyped
   megacaps and meme names. Define the universe from a fixed reference set
   (e.g. S&P 500 / Russell 1000 constituents) and treat social activity as a
   sentiment *overlay feature* rather than the gatekeeper of what gets analyzed.

Further hardening (lower priority): finance-tuned sentiment (FinBERT) with
bot/spam filtering and low-sample shrinkage; fundamentals plausibility/outlier
validation and a second data source; an authoritative exchange ticker master;
and benchmark-relative framing (sector-percentile ranks) in the email.
