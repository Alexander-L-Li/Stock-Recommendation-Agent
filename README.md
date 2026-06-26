# Daily Long-Term Stock Recommendation Agent

An autonomous, **free** agent that runs once daily, screens a fixed universe
(the **S&P 500**), scores candidates primarily on **financial fundamentals**
(70%, ranked **sector-relative**) with **social/news sentiment** as a secondary
tilt (30%) and a **price-based risk/momentum** adjustment, and emails a ranked,
explainable report. Social chatter from **StockTwits**, **Reddit**, and business
news is a prioritization + sentiment *overlay*, not the gatekeeper of what gets
analyzed. Long-term horizon, no trading, personal use.

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
  fundamentals/          # yfinance_fetcher.py + price_factors.py (risk/momentum)
  scoring/               # engine.py (long-term 70/30 + hype gate + sector-relative
                         #   + risk tilt); short_term.py (1-3 mo momentum/technical)
  analysis/              # backtest.py (forward-return attribution vs SPY)
  report/                # builder.py (HTML + plain text), backtest_report.py
  delivery/              # ses_sender.py
  storage/               # dynamo.py (single-table: watchlist + history + snapshots)
  universe.py            # FixedUniverse selector (#5)
  universe_data.py       # vendored S&P 500 constituents
  orchestrator.py        # wires the pipeline; build_default() for production
  lambda_handler.py      # daily entry point + weekly backtest_handler + error alerting
  cli.py                 # local CLI + watchlist/holdings management + backtest
infra/template.yaml      # SAM template (Lambda + EventBridge + DynamoDB + alarm/SNS)
deploy/build_lambda.sh   # builds the Lambda zip (manylinux wheels for numpy/pandas)
deploy/deploy_aws.sh     # one-shot deploy via AWS CLI (no SAM/Docker required)
docs/SES_SETUP.md        # SES sandbox setup (email yourself)
docs/DEPLOYMENT.md       # full deploy walkthrough
tests/                   # 167 tests, fully mocked (no network/AWS)
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

# Track stocks you own — they always get a dedicated "Your Holdings" section
# in the daily report (sentiment, news, risk, and a buy/hold/trim signal).
python -m stock_agent.cli holdings add NVDA
python -m stock_agent.cli holdings add AAPL
python -m stock_agent.cli holdings list
python -m stock_agent.cli holdings remove AAPL

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
| `ENABLE_FIXED_UNIVERSE` | true | Analyze the S&P 500 index (#5); social is an overlay |
| `ENABLE_SECTOR_RELATIVE` | true | Score growth/quality by sector percentile (#3) |
| `SECTOR_MIN_PEERS` | 4 | Min sector cohort size before sector-relative kicks in |
| `ENABLE_PRICE_FACTORS` | true | Apply price-based risk/momentum tilt (#4) |
| `RISK_TILT_MAX` | 10 | Max +/- points the risk tilt can move a score |
| `MIN_DOLLAR_VOLUME` | 2,000,000 | Liquidity floor ($/day); thinner names can't be boosted |
| `PRICE_BENCHMARK` | SPY | Benchmark for beta and backtest excess return |
| `TOP_N` | 10 | Picks in the report |
| `ENABLE_HOLDINGS` | true | Render a dedicated "Your Holdings" tracker section |
| `ENABLE_SHORT_TERM` | true | Render the short-term (1-3 mo) momentum/technical picks section |
| `SHORT_TERM_TOP_N` | 5 | Number of short-term picks to show |

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
- **Holdings (portfolio tracker)** — managed via the CLI
  (`holdings add/remove/list`). Held tickers are *always* analyzed and rendered
  in a dedicated **Your Holdings** section of the report — current price, blended
  score, social/news sentiment, risk & momentum, recent headlines, and a
  rule-based **ADD / HOLD / TRIM / WATCH** signal (`report.builder.holding_signal`)
  with a short reason — regardless of whether they rank in the day's top picks.
  Toggle with `ENABLE_HOLDINGS`. The signal is a transparent cue, not advice.
  Held tickers are **excluded from the long-term top picks** (they have their own
  section), so the same name never appears twice.
- **Short-term picks (1-3 months)** — a separate momentum/technical evaluator
  (`scoring/short_term.py`) rendered in its own report section. It flips the
  long-term model's emphasis: momentum (1m/3m returns) 35%, sentiment/news 25%,
  technical posture (RSI, 52-week-high proximity, volume) 15%, trend
  (price vs SMA50/SMA200) 15%, earnings momentum 10% — with falling-knife,
  volatility, and liquidity risk guards. Emits a **STRONG BUY / BUY / WATCH /
  AVOID** entry signal. Toggle with `ENABLE_SHORT_TERM`.

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

## Sector-relative scoring, risk factors & a fixed universe (#3–#5)

Quant-reviewed credibility upgrades — **now implemented** (the backtest above is
what makes their impact measurable over time):

1. **Sector-relative, cross-sectional scoring (#3)** — `scoring/engine.py`.
   Growth/quality metrics (revenue & earnings growth, profit margin, ROE,
   debt/equity) are scored by **percentile rank within the candidate's GICS
   sector** across the whole cohort, instead of fixed linear thresholds, so we
   stop comparing a bank's ROE to a software firm's margin. Valuation (PEG/P/E)
   and the free-cash-flow quality check keep their absolute treatment, and a
   sector with fewer than `SECTOR_MIN_PEERS` (default 4) names gracefully falls
   back to absolute scaling. Toggle with `ENABLE_SECTOR_RELATIVE`.
2. **Price-based risk & momentum factors (#4)** — `fundamentals/price_factors.py`.
   12‑1 month momentum, annualized volatility, beta vs SPY, max drawdown, and a
   dollar-volume liquidity proxy are computed from yfinance `.history()`. They
   apply a **bounded risk tilt** (±`RISK_TILT_MAX`, default 10 pts) on top of the
   70/30 blend — momentum lifts, high volatility/deep drawdowns drag — and a thin
   liquidity name can never be *boosted*. Factors are shown in the email and
   persisted in the snapshot for attribution. Toggle with `ENABLE_PRICE_FACTORS`.
3. **Decoupled universe (#5)** — `universe.py` + `universe_data.py`. The analysis
   universe is now the **fixed S&P 500** (vendored), not whatever social media
   happens to mention. Social activity is a *prioritization overlay* (which index
   names get screened first under the per-run cap); the rest of the index rotates
   by date for full coverage over ~2 weeks. The watchlist is always included and
   is the escape hatch for off-index names. Toggle with `ENABLE_FIXED_UNIVERSE`.

> Refresh the vendored S&P 500 list by re-downloading the constituents CSV and
> regenerating `src/stock_agent/universe_data.py` (symbols in yfinance form, e.g.
> `BRK-B`).

Further hardening (still on the roadmap): finance-tuned sentiment (FinBERT) with
bot/spam filtering and low-sample shrinkage; fundamentals plausibility/outlier
validation and a second data source; an authoritative exchange ticker master;
and benchmark-relative framing (sector-percentile ranks) in the email.
