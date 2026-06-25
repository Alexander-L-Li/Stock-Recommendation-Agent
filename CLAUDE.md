# CLAUDE.md — Agent Handoff & Working Notes

This file is the working memory for AI agents (and humans) continuing development
on the **stock-recommendation-agent**. It documents the architecture, everything
that has been built so far, how to build/test/deploy, the operational facts you
need (AWS, GitHub), and what is left on the roadmap. Read this top-to-bottom
before making changes.

> If anything here conflicts with the code, **the code wins** — update this file.

---

## 1. What this project is

An autonomous, **free** agent that runs **once daily** as an AWS Lambda. It:

1. Screens a **fixed universe** (the **S&P 500**, vendored locally).
2. Pulls **fundamentals** (yfinance) and **price history** for the candidates.
3. Pulls **social/news sentiment** (StockTwits + optional Reddit + RSS news) as
   an *overlay*, not as the universe gatekeeper.
4. Scores each candidate: **70% fundamentals / 30% sentiment**, with
   fundamentals ranked **sector-relative**, a **hype gate**, and a bounded
   **price risk/momentum tilt**.
5. Emails a ranked, explainable HTML+text report (SES), with a dedicated
   **Your Holdings** tracker section for stocks the owner holds.
6. Persists a **point-in-time snapshot** of every pick to DynamoDB for a weekly
   **backtest** (forward returns vs SPY).

Long-term horizon, no trading, personal use. Tagline in the report:
*"Signals and reasoning to aid your own research — not financial advice."*

- **Local repo:** `~/StockAgent/stock-recommendation-agent`
- **GitHub:** `https://github.com/Alexander-L-Li/Stock-Recommendation-Agent` (branch `main`)
- **Language:** Python 3.14, virtualenv at `.venv/`
- **Tests:** 150, fully mocked (no network/AWS)

---

## 2. Repository layout

```
src/stock_agent/
  config.py              # env-driven Config dataclass + validation + from_env()
  models.py              # dataclasses: Fundamentals, SentimentResult, ScoredCandidate,
                         #   PriceFactors, RedditPost/SocialPost, NewsArticle/NewsRef, Report
  collectors/            # stocktwits_collector.py, reddit_collector.py,
                         #   news_collector.py (RSS), ticker_news.py (per-ticker headlines)
  extraction/            # ticker_extractor.py (+ tickers_data.py allow/deny lists)
  sentiment/             # base.py (SentimentAggregator + analyzer interface), vader.py
  fundamentals/          # yfinance_fetcher.py (caching, missing-data handling)
                         # price_factors.py  <-- #4 momentum/vol/beta/drawdown/liquidity
  scoring/               # engine.py  <-- 70/30 + hype gate + #3 sector-relative + #4 risk tilt
  analysis/              # backtest.py (forward-return attribution vs SPY)
  report/                # builder.py (HTML + plain text), backtest_report.py
  delivery/              # ses_sender.py
  storage/               # dynamo.py (single-table: watchlist + history + snapshots)
  universe.py            # #5 FixedUniverse selector (social overlay + rotation)
  universe_data.py       # #5 vendored S&P 500 constituents (503 symbols)
  orchestrator.py        # wires the pipeline; build_default() for production
  lambda_handler.py      # daily entry point + weekly backtest_handler + error alerting
  cli.py                 # local CLI: preview / run / watchlist mgmt / backtest
infra/template.yaml      # SAM template (Lambda + EventBridge + DynamoDB + alarm/SNS)
deploy/build_lambda.sh   # builds the Lambda zip (manylinux wheels for numpy/pandas)
deploy/deploy_aws.sh     # one-shot deploy via AWS CLI (no SAM/Docker required)
docs/SES_SETUP.md        # SES sandbox setup
docs/DEPLOYMENT.md       # full deploy walkthrough
tests/                   # 150 tests, fully mocked
```

---

## 3. Pipeline / data flow (orchestrator.run)

`Orchestrator.run(run_date, send_email=True, persist=True)` steps:

1. Collect social posts (StockTwits always; Reddit if enabled) + RSS news.
2. Extract tickers + aggregate sentiment per ticker (`SentimentAggregator`).
3. **Select candidates:**
   - If a `universe_provider` is wired (production default), call
     `self.universe.select(mention_counts, watchlist, max_candidates, run_date)`
     — fixed S&P 500 with social as a prioritization overlay (**#5**).
   - Else fall back to `extractor.discover(...)` (legacy social-discovery path;
     this is what the e2e tests without providers exercise).
4. Fetch fundamentals for the candidates (`fundamentals_fetcher.fetch_many`).
5. Attach per-ticker news (RSS + optional `news_provider`, deduped).
   **5b.** If a `factor_fetcher` is wired, fetch price factors
   (`factor_fetcher.fetch_many(candidates)`), wrapped in try/except so a
   yfinance hiccup never breaks the run (**#4**).
6. `engine.rank(fundamentals, sentiment, factors)` -> ranked `ScoredCandidate`s.
7. Build the report; optionally email (SES) and persist (DynamoDB).

`build_default(config)` wires production collaborators, **including**
`FixedUniverse()` when `config.enable_fixed_universe` and
`PriceFactorFetcher(benchmark=config.price_benchmark)` when
`config.enable_price_factors`.

---

## 4. Scoring model (scoring/engine.py)

- **Final score = 0.70 * fundamentals_score + 0.30 * sentiment_score**, then the
  **hype gate**: if fundamentals < `HYPE_GATE_MIN_FUNDAMENTALS` (default 40), the
  sentiment lift is suppressed (prevents meme pumps from ranking high).
- **fundamentals_score** = mean of subscores. Full data => `len(subs) == 7`
  (revenue_growth, earnings_growth, profit_margin, roe, debt_to_equity,
  valuation [PEG/PE], free_cash_flow). **A `_strong()` candidate must score > 75**
  (asserted by `tests/test_scoring_subscores.py` — keep this invariant).

### #3 Sector-relative (cross-sectional) scoring
- `rank()` computes `overrides = self._cross_sectional_subscores(fundamentals)`
  when `config.enable_sector_relative`, and threads `relative_subs` into each
  `score_candidate` / `score_fundamentals`.
- `_cross_sectional_subscores`: groups candidates by `f.sector` (errored excluded),
  and for each of `_RELATIVE_METRICS` = (revenue_growth +1, earnings_growth +1,
  profit_margin +1, roe +1, debt_to_equity -1) computes `_percentile_scores`
  **only if** the cohort has >= `config.sector_min_peers` (default 4) values.
  Returns `defaultdict(ticker -> {metric -> score})`.
- `_percentile_scores(pairs, direction)`: **midrank percentile**
  `(less + 0.5*equal)/n*100`, inverted when `direction < 0`.
  Sanity: 4 ascending values dir+1 -> top 87.5 / bottom 12.5; 2 values -> 75/25.
- `score_fundamentals(f, relative_subs=None)` uses `pick(key, absolute) =
  rel[key] if key in rel else absolute`. **Valuation and free_cash_flow stay
  absolute.** Backward-compatible: called without `relative_subs` => fully absolute.

### #4 Price risk/momentum tilt
- `score_candidate(ticker, fundamentals, sentiment, relative_subs=None,
  factors=None)`: after the gate, if `factors` present + no error +
  `config.enable_price_factors`, apply `_risk_tilt(factors)` and store `factors`
  on the candidate.
- `_risk_tilt(factors) -> (tilt, risk_score)`: components via `_scale`
  (momentum -0.30..0.50 ; volatility 0.80..0.15 **inverted** ; drawdown
  -0.60..-0.05), weighted `_RISK_WEIGHTS = {momentum 0.50, volatility 0.25,
  drawdown 0.25}`. `tilt = (risk_score - 50)/50 * config.risk_tilt_max`.
  **Illiquid** (`avg_dollar_volume < config.min_dollar_volume`) caps
  `tilt = min(tilt, 0)` — a thin name can never be *boosted*.
- `_explain_factors(cand, signals, risks)`: momentum >= .15 signal / <= -.15 risk;
  volatility >= .50 risk; drawdown <= -.35 risk; thin liquidity risk. Called from
  `_explain`.

---

## 5. Price factors (fundamentals/price_factors.py) — #4

- `PriceSeries(closes, volumes)` dataclass.
- `_default_series_factory`: yfinance `.history(period="2y", auto_adjust=True)`.
- Pure-python helpers: `_daily_returns`, `_mean`, `_stdev`. Constants
  `_MONTH = 21`, `_YEAR = 252`.
- `PriceFactorFetcher(series_factory=None, benchmark="SPY",
  cache_ttl_seconds=3600)` with `.fetch(ticker)` and `.fetch_many(tickers)`.
  - **momentum** = 12‑1 month: `closes[-_MONTH-1] / closes[-1-_YEAR] - 1`.
  - **volatility** = stdev(daily returns) * sqrt(252).
  - **beta** vs benchmark daily returns (cov/var).
  - **max_drawdown** over the last ~252 closes.
  - **avg_dollar_volume** = mean(close*volume) over last ~21 days.
  - Needs `>= _MONTH + 5` closes else `error = "insufficient history"`; any
    factory exception is captured into `PriceFactors.error` (never raised).
  - **Injectable `series_factory`** is the test seam — tests pass a function that
    returns constructed `PriceSeries` for the ticker and `"SPY"`.

`models.PriceFactors`: `ticker, momentum, volatility, beta, max_drawdown,
avg_dollar_volume, error`, plus `available_count()`. `ScoredCandidate.factors:
Optional[PriceFactors] = None`.

---

## 6. Fixed universe (universe.py + universe_data.py) — #5

- `universe_data.SP500`: `tuple[str, ...]` of **503** symbols, sourced from the
  public dataset CSV
  `https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv`
  and normalized to yfinance form (`.` -> `-`, e.g. `BRK-B`, `BF-B`).
- `FixedUniverse(tickers=None)` defaults to `SP500`. Provides `__len__`,
  `__contains__`, `.tickers`, and:
  - `.select(mention_counts=None, watchlist=None, max_candidates=40,
    run_date=None)` with priority:
    1. **Watchlist** always included (cap-exempt; the off-index escape hatch).
    2. **Social overlay**: index members with mention_count > 0, sorted by count
       desc then alpha.
    3. **Rotating coverage** of the rest via `_rotate(items, run_date)` using
       `date.fromisoformat(run_date).toordinal() * 25 % len` — deterministic per
       date, covers the whole index over ~2 weeks. Bounded by `max_candidates`.
    - Off-index social names are ignored (watchlist is the only way to force one).

### Refreshing the S&P 500 list (manual, infrequent)
```bash
curl -sL "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv" -o /tmp/sp500.csv
# then regenerate src/stock_agent/universe_data.py from the Symbol column,
# replacing "." with "-" for yfinance compatibility.
```

---

## 7. Report (report/builder.py)

- `_risk_line(pf)` module helper renders a compact line:
  `+134% 12-mo · 31% vol · β 1.10 · -20% maxDD · $12.77B/day`.
- Text body: `   Risk: ...` after the headline.
- HTML body: `Risk & momentum: ...` gray line after the headline strip.
- Headline strip shows sector · price · market cap · upside-to-target · PEG/PE.

---

## 8. Storage (storage/dynamo.py)

- Single DynamoDB table `stock-agent`, PAY_PER_REQUEST.
- Item kinds: `WATCHLIST#`, `HOLDINGS#`, `RUN#<date>` + `PICK#<rank>#<symbol>` +
  `META`, ticker history (`TICKER#<sym>`/`RUN#<date>`), `RUNS` index.
- **`save_run` is idempotent** — it calls `_clear_run(run_date)` first, deleting
  any prior `PICK#` rows + per-ticker history for that date before writing. This
  prevents duplicate-pick accumulation on same-day re-runs (the `PICK#` SK embeds
  rank+symbol, which shifts run-to-run, so plain puts would NOT overwrite). Keep
  this — there's a `test_save_run_is_idempotent_for_same_date` regression test.
- Watchlist + holdings: `add_to_watchlist/remove_from_watchlist/list_watchlist`
  and `add_holding/remove_holding/list_holdings` (both upper-cased + sorted).
- `_feature_snapshot(c)` captures fundamentals + sentiment vectors and, when
  `c.factors` is present **and has no error**, a `snap["factors"]` block
  (momentum/volatility/beta/max_drawdown/avg_dollar_volume, non-None only).
- `_to_dynamo` guards against **non-finite floats** (NaN/inf) — `yfinance_fetcher._f`
  also rejects non-finite values. Don't regress this (there's a storage test).
- `entry_price`, `sector`, `market_cap` are promoted to top-level pick fields for
  the backtest.

---

## 8b. Holdings (portfolio tracker)

Lets the owner track stocks they hold with a dedicated daily report section.

- **Storage:** `HOLDINGS` partition (above). CLI: `holdings add/remove/list`
  (`cli.cmd_holdings`, mirrors watchlist).
- **Orchestrator:** reads holdings (guarded by `enable_holdings` +
  `hasattr(store, "list_holdings")`), unions watchlist+holdings into an
  `always_include` set passed to StockTwits symbol selection and the universe
  selector so holdings are **always fetched, scored, factored, and news-attached**
  regardless of rank or social buzz. Builds `holdings_cands` from
  `{ranked+excluded by ticker}` and passes `holdings=` to the report.
- **Report:** `report.builder.holding_signal(c) -> (label, reason)` returns
  **ADD / HOLD / TRIM / WATCH** (thresholds: strong score >=70, weak <45; sentiment
  pos >=55, soft <45; momentum +/-0.15; strong+confirming -> ADD; weak or
  (neg-momentum AND soft-sentiment) -> TRIM; no fundamentals -> WATCH). Rendered as
  a "Your Holdings" section (text + HTML, colored badge) FIRST, before the day's
  top picks. Toggle: `ENABLE_HOLDINGS`.

---

## 9. Config (config.py)

`Config` dataclass + `from_env()`. Env vars (defaults in parens):

| Env var | Default | Meaning |
|---|---|---|
| `TABLE_NAME` | stock-agent | DynamoDB table |
| `AWS_REGION` | us-east-1 | DynamoDB + SES region |
| `SENDER_EMAIL` / `RECIPIENT_EMAILS` | — | SES-verified addresses |
| `ENABLE_STOCKTWITS` | true | StockTwits collector |
| `STOCKTWITS_SYMBOL_LIMIT` | 20 | Max symbols fetched/run |
| `ENABLE_REDDIT` | true* | Reddit collector (*disabled in prod; needs approved creds) |
| `FUNDAMENTALS_WEIGHT` / `SENTIMENT_WEIGHT` | 0.70 / 0.30 | Must sum to 1.0 |
| `HYPE_GATE_MIN_FUNDAMENTALS` | 40 | Min fundamentals to allow sentiment lift |
| `MIN_FUNDAMENTAL_METRICS` | 3 | Data-quality gate |
| `MAX_CANDIDATES` | 40 | Per-run cap (the universe is the *pool*) |
| `ENABLE_FIXED_UNIVERSE` | true | **#5** analyze S&P 500; social is overlay |
| `ENABLE_SECTOR_RELATIVE` | true | **#3** sector-percentile growth/quality |
| `SECTOR_MIN_PEERS` | 4 | Min cohort size before sector-relative applies |
| `ENABLE_PRICE_FACTORS` | true | **#4** apply risk/momentum tilt |
| `RISK_TILT_MAX` | 10.0 | Max +/- pts the tilt can move a score |
| `MIN_DOLLAR_VOLUME` | 2,000,000 | Liquidity floor; thinner names can't be boosted |
| `PRICE_BENCHMARK` | SPY | Beta + backtest benchmark |
| `TOP_N` | 10 | Picks in the report |
| `ENABLE_HOLDINGS` | true | Render the "Your Holdings" portfolio-tracker section |

All new flags default to enabled in code, so the deployed Lambda picked them up
without any env changes.

---

## 10. Build / test / run

```bash
cd ~/StockAgent/stock-recommendation-agent

# Run the full suite (150 tests). PYTHONPATH=src needed for ad-hoc -c imports.
.venv/bin/python -m pytest -p no:cacheprovider --color=no

# Run a single file
.venv/bin/python -m pytest -p no:cacheprovider --color=no -q tests/test_universe.py

# Local preview (writes an HTML report; no email/persist). Needs network for
# yfinance + AWS creds if it reads the DynamoDB watchlist. Cap candidates to
# keep it fast:
export PYTHONPATH=src TABLE_NAME=stock-agent AWS_REGION=us-east-1 \
  SENDER_EMAIL=<you> RECIPIENT_EMAILS=<you> ENABLE_REDDIT=false \
  ENABLE_STOCKTWITS=true MAX_CANDIDATES=10
.venv/bin/python -m stock_agent.cli preview --out /tmp/report.html
```

### Test invariants to preserve
- `score_candidate` / `score_fundamentals` called **directly** (no `relative_subs`,
  no `factors`) must stay **absolute** — many tests depend on this.
- Full-data candidate => `len(subs) == 7`; `_strong()` => score > 75.
- The e2e harness (`tests/test_orchestrator_e2e.py::_build`) builds the
  Orchestrator **without** `universe_provider` / `factor_fetcher`, so it uses the
  social-discovery path and no factors. Inject fakes in a test if you need the
  fixed-universe / factor path (see the #4/#5 integration tests there).
- 3-candidate scoring tests use `sector=None` => single "—" cohort of size 3 <
  `sector_min_peers` => absolute fallback => ordering preserved.

---

## 11. Deploy (AWS)

**Account `<aws-account-id>` (ask the repo owner), region `us-east-1`, function
`stock-agent`.**
Credentials are short-lived (~1h). Re-vend via the sandbox creds tool
(role `Admin`) and export the returned paths:

```bash
export AWS_SHARED_CREDENTIALS_FILE=<creds path from creds tool>
export AWS_CONFIG_FILE=<config path from creds tool>
```

Build + deploy:
```bash
cd ~/StockAgent/stock-recommendation-agent
PYTHON=.venv/bin/python bash deploy/build_lambda.sh        # -> build/stock-agent-lambda.zip (~37MB)
aws lambda update-function-code --function-name stock-agent \
  --zip-file fileb://build/stock-agent-lambda.zip --region us-east-1
aws lambda wait function-updated --function-name stock-agent --region us-east-1
```

Invoke (CLI v2 needs `--cli-binary-format raw-in-base64-out` if you pass a
non-`{}` payload):
```bash
# Daily run
aws lambda invoke --function-name stock-agent --region us-east-1 \
  --payload '{}' /tmp/out.json && cat /tmp/out.json
# Weekly backtest
aws lambda invoke --function-name stock-agent --region us-east-1 \
  --cli-binary-format raw-in-base64-out --payload '{"mode":"backtest"}' /tmp/bt.json
```

**Lambda config:** timeout **600s**, memory **1024MB** (raised from 300s/512MB
because the daily run now fetches ~40 candidate price histories + the benchmark;
a real run completes in ~30s, so there's ample headroom).

**EventBridge:** daily schedule rule (+ optional weekly backtest rule). SES is in
sandbox — sender and recipient must be verified addresses.

Verify a run persisted factors:
```bash
aws dynamodb query --table-name stock-agent --region us-east-1 \
  --key-condition-expression "PK = :pk AND begins_with(SK, :sk)" \
  --expression-attribute-values '{":pk":{"S":"RUN#<date>"},":sk":{"S":"PICK#001"}}' \
  --query 'Items[0].{ticker:ticker.S,sector:sector.S,momentum:snapshot.M.factors.M.momentum.N}'
```

---

## 12. Git / GitHub conventions

- Commit identity: **`Alexander Li <alxli@mit.edu>`**. Branch **`main`**.
- Conventional Commit messages (`feat:`, `fix:`, `test:`, `docs:`...).
- `origin` is **token-free**; push with a one-off authenticated URL using a
  GitHub fine-grained PAT (Contents: Read/Write):
  ```bash
  export GH_TOKEN='<fine-grained PAT>'   # never commit/echo this
  git push "https://x-access-token:${GH_TOKEN}@github.com/Alexander-L-Li/Stock-Recommendation-Agent.git" main:main
  ```
  **Confirm/rotate the PAT with the repo owner before relying on it; never print
  the token in command output (pipe through `sed "s/${GH_TOKEN}/<redacted>/g"`).**
- Never force-push; remote history is immutable. Fix forward with new commits.
- Run a secret scan before committing:
  ```bash
  grep -rnE "github_pat_|AKIA[0-9A-Z]{16}|<account-id>" --include="*.py" \
    --include="*.md" --include="*.sh" src tests deploy README.md
  ```

---

## 13. Project history (chronological)

1. **Initial agent** (`6f88c9f` feat: daily long-term stock recommendation agent)
   — social discovery (StockTwits/Reddit/news) -> fundamentals -> 70/30 scoring +
   hype gate -> SES email; DynamoDB watchlist + history; SAM/CLI deploy; CLI.

2. **Backtesting + snapshots + richer email** (`7a99115`) — point-in-time feature
   snapshots persisted per pick; `analysis/backtest.py` forward-return attribution
   vs SPY; weekly `backtest_handler`; per-ticker news enrichment + headline strips
   in the report; non-finite float guards in storage/fetcher.

3. **#3/#4/#5 credibility upgrades** (`e97008c`, current HEAD) — this work:
   - **#3** sector-relative cross-sectional scoring.
   - **#4** price risk/momentum factors + bounded tilt + snapshot persistence.
   - **#5** fixed S&P 500 universe with social as a prioritization overlay.
   - Report risk/momentum line; 7 new config flags; **+29 tests (135 total)**;
     README moved #3-5 from roadmap to implemented.
   - Deployed + verified live: daily invoke `200`, ranked 39 from the S&P 500,
     emailed, ~29s; top pick GOOGL snapshot persisted sector + factors
     (momentum 1.337 / vol 0.308 / beta 1.10). Lambda raised to 600s/1024MB.

4. **Holdings tracker + idempotent save_run** (latest) — dedicated "Your Holdings"
   report section for owned stocks (always-shown sentiment/news/risk +
   ADD/HOLD/TRIM/WATCH signal); `holdings` CLI; `HOLDINGS` storage partition;
   `save_run` made idempotent via `_clear_run`. Also cleaned up 119 duplicate
   `PICK#` rows + 66 stale per-ticker history rows under `RUN#2026-06-25` left by
   repeated same-day manual test invocations. **+15 tests (150 total)**. Verified
   live: NVDA->ADD, AAPL/KO->HOLD, INTC->TRIM render with real data; re-run kept
   the partition at exactly 40 picks (idempotency holds).

---

## 14. Known data quirks / gotchas

- **yfinance history outliers:** occasionally returns extreme values (e.g. a
  `+3258%` 12‑mo momentum from a split/IPO artifact). Handled safely today — the
  risk tilt is **bounded ±`RISK_TILT_MAX`** so it can't dominate, and high
  volatility is flagged — but price-history outlier validation is a good
  follow-up.
- **yfinance rate/flakiness:** factor fetch is best-effort (try/except in the
  orchestrator) and per-symbol errors are captured into `PriceFactors.error`, so
  a bad symbol degrades gracefully instead of failing the run.
- **SES sandbox:** only verified addresses can send/receive.
- **AWS creds expire (~1h):** re-vend and re-export the two env files mid-session.

---

## 15. Roadmap (still open — "further hardening")

In rough priority order:

1. **Finance-tuned sentiment (FinBERT)** with bot/spam filtering and low-sample
   shrinkage (replace/augment VADER).
2. **Fundamentals plausibility/outlier validation** + a **second data source**
   (cross-check yfinance).
3. **Price-history outlier validation** (winsorize/clamp momentum & vol inputs).
4. **Authoritative exchange ticker master** (replace the hand-maintained
   allow/deny lists in `extraction/tickers_data.py`).
5. **Benchmark-relative framing in the email** (show sector-percentile ranks /
   excess-return context next to each pick).
6. Periodic **auto-refresh of the vendored S&P 500** list.

When you implement one, add tests, run the full suite green, deploy + verify
live, update the README (move it from roadmap to implemented), and **update this
CLAUDE.md**.
