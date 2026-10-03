# Sector Scout

A weekly pipeline that finds market sectors with bullish swing-trading conviction from news and Reddit, then analyses the stocks inside those sectors (the FTSE 350 and the S&P 500) and picks a few to trade over a **2–6 week horizon**. It runs on AWS, costs essentially nothing, stores every week's results so the methodology can be tested against what the market did, and emails one report a week.

Vocabulary used throughout (Universe, Candidate, Pick, Hit, ...) is defined in [`CONTEXT.md`](CONTEXT.md).

---

## How it fits together

```
Saturday 06:00 UTC (EventBridge Scheduler)
        │
        ▼
 sector-scout Lambda ── news + Reddit ─► 3 LLM passes ─► Sector Signals ─► s3://…/successful/<week>/response_*.json
                                                                                     │ (S3 event)
                                                                                     ▼
                                                                         stock-selection Lambda
                                              prices for 853 stocks ─► trend + risk analysis ─► Picks per signalled sector
                                                                                     │
                                          s3://…/snapshots/<week>/universe.csv ◄─────┤
                                                                                     ▼
                                                                  ONE email: sector report with the picks inside each sector

1 Jan / Apr / Jul / Oct: backtest-report Lambda ─► reads all snapshots ─► reports/<quarter>/ + email ─► methodology review (a pull request)
```

| Part | Where | What it does |
|---|---|---|
| Sector scout | `financial_market_news_analyzer/` | News and Reddit in, Sector Signals out (saved to S3; sends no email) |
| Stock selection | `lse_stock_analysis/`, `stock_selection_lambda/` | Prices, analysis, rule-based Picks, the weekly Universe Snapshot, and the one email |
| Backtest and review | `lse_stock_analysis/backtest.py`, `stock_selection_lambda/backtest_*.py`, `docs/methodology-review.md` | Compares Picks and Candidates with the market, quarterly |
| Infrastructure | `infra/` | Terraform for everything in AWS (see [`infra/README.md`](infra/README.md)) |

The two Lambdas are deliberately separate: the scout's weekly-window and retry logic stays simple, and stock selection can use heavy dependencies (pandas, numpy, yfinance) and its own timeout ([ADR 0001](docs/adr/0001-stock-selection-runs-as-separate-lambda.md)). The scout's saved JSON is the contract between them.

---

## The weekly email

One email per week, sent by the stock-selection Lambda: the scout's macro regime and one card per Sector Signal (strongest first) with bull/bear bars, mechanism, kill switch, catalysts and evidence, and **the stock Picks inside each card** (ticker, market, setup, grade, risk, stop-loss, maximum position, close in its own currency), the close runners-up and why they missed, and any data warnings.

It still goes out when the stock side has problems. If Yahoo is down, hangs, or returns too little data, you get the full sector report with a "stock picks failed" banner instead of picks (a download attempt is abandoned after 240 s so a hang cannot use up the Lambda's time). Only if the stock-selection Lambda itself does not run is there no email.

---

## Part 1: the sector scout

### Three LLM passes

```
RSS feeds + HL scrape ──► LLM Pass 1 (news)   ─┐
                                                 ├─► LLM Pass 3 (consolidate) ─► ConsolidatedSignals
Reddit RSS             ──► LLM Pass 2 (reddit) ─┘
```

**Pass 1: news analysis** (`llm_analyzer.py`). Scrapes RSS feeds and the HL Weekly Outlook, then sends headlines to an LLM instructed to act as a senior portfolio manager. Returns structured signals with sector, confidence, disruption type, propagation mechanism and conviction drivers.

**Pass 2: Reddit sentiment** (`reddit_analyzer.py`). Pulls posts from finance and trading subreddits via RSS (no API key). A separate LLM pass extracts genuine bullish retail conviction, graded by quality (DD, Discussion, News, Meme, Mixed).

**Pass 3: consolidation** (`consolidator.py`). Merges both and labels each signal:
- `Convergent`: news thesis and retail conviction agree; confidence boosted up to +0.10
- `News-Led`: strong news thesis, Reddit absent or neutral
- `Reddit-Led`: strong retail DD with no news confirmation (haircut applied)
- `Divergent`: news bullish, Reddit explicitly bearish; confidence discounted

### Models

Every LLM call goes through OpenRouter. The model list lives only in `config.py` under `OPENROUTER_MODELS`: the primary is `openai/gpt-6-luna-pro`, with `deepseek/deepseek-v4.1-flash` as fallback. Both are fixed model IDs (no `-latest` aliases, so each week's signals trace to one model version), and every request disables reasoning, because with reasoning on both models intermittently exhausted `max_tokens` and returned truncated or empty JSON. The pipeline chunks models in groups of three and uses OpenRouter's native fallback (`route: "fallback"`) within each chunk. `PROMPT_VERSION` in `config.py` is saved with every result (see Methodology versions).

### Sectors and disruption types

The 31 sectors the scout uses (`LSE_SECTORS` in `config.py`) span Technology, Financials, Energy, Healthcare, Consumer, Industrials, Materials, Real Estate, Utilities and Telecoms. Three are UK-specific (UK Retail Banks, UK General Retail, UK Telecoms & Broadband). The eight disruption types are Supply Chain Dislocation, Regulatory / Policy Shift, Macro Regime Change, Geopolitical Shock, Technology / Adoption Inflection, Earnings / Guidance Divergence, Commodity Price Inflection, and M&A / Consolidation Wave.

### Signal fields

| Field | Type | Description |
|---|---|---|
| `sector` | `str` | Sector name (exact from the taxonomy) |
| `confidence` | `float` | 0–1, consolidated LLM conviction |
| `disruption_type` | `str` | Disruption category |
| `disruption_strength` | `float` | 0–1, magnitude of the structural break |
| `time_to_impact_weeks` | `int` | Estimated weeks before full market pricing |
| `bear_case_probability` | `float` | Probability of a negative or flat outcome |
| `convergence_type` | `str` | `Convergent` \| `News-Led` \| `Reddit-Led` \| `Divergent` |
| `convergence_note` | `str` | Why news and Reddit agree or disagree |
| `propagation` | `str` | Mechanism sentence |
| `invalidation_risk` | `str` | What kills the thesis, with probability |
| `rationale` | `str` | Consolidated investment case |
| `retail_thesis` | `str` | What the Reddit crowd believes |
| `key_catalysts` | `list` | Upcoming confirming events |
| `correlated_sectors` | `list` | Secondary sector plays (0–2) |
| `conviction_drivers` | `list` | Evidence items tagged `[Source: News\|Reddit]` |
| `macro_regime_summary` | `str` | Macro backdrop at the time |
| `timestamp` | `datetime` | UTC timestamp |

### Data sources

**News:** 20+ RSS feeds including BBC Business, Guardian, City A.M., Sky News, AP, Yahoo Finance, Bank of England, Investing.com, Investegate RNS and Proactive Investors, plus the HL Weekly Outlook. **Reddit:** UKInvesting, UKPersonalFinance, investing, stocks, SecurityAnalysis, Economics, Commodities, energy, wallstreetbets, options and thetagang, all via public RSS.

### Failure modes

| Scenario | Behaviour |
|---|---|
| News LLM fails | Keyword heuristic fallback used; `llm_succeeded=False` |
| Reddit unreachable | Pipeline continues with news-only input |
| Reddit LLM fails | Consolidator runs with no Reddit signals |
| Consolidator fails | Raw news signals wrapped as `News-Led` ConsolidatedSignals; `llm_succeeded=False` |
| All passes fail | Returns `([], False)`; nothing is saved as a success and the hourly retry tries again |

A weekly window opens when the Saturday schedule fires and closes after the first success; a retry runs every 30 minutes (at :05 and :35) while it is open and otherwise returns immediately.

### Running the scout locally

```bash
pip install feedparser requests beautifulsoup4 lxml boto3
export OPENROUTER_API_KEY=sk-or-...      # in AWS the key comes from SSM /sector-scout/openrouter-api-key

cd financial_market_news_analyzer
python sector_scout.py                   # one-shot run
python sector_scout.py --skip-reddit     # news only, faster
python sector_scout.py --verbose         # intermediate signal counts for all three passes
```

```python
from sector_scout import SectorScout
signals, llm_succeeded = SectorScout().run_cycle()     # llm_succeeded is False if consolidation fell back
result = SectorScout().run_cycle_verbose()             # signals, llm_succeeded, news_signals, reddit_signals, macro_regime
```

Thresholds are in `config.py`: `SCHEDULER_CFG.min_confidence` 0.55, `min_disruption_strength` 0.50, `REDDIT_CFG.min_reddit_conviction` 0.40, at most 500 headlines and 200 posts per cycle.

---

## Part 2: stock selection

### The Universe

The **Universe** is 853 stocks: the FTSE 350 (market `LSE`, quoted in pence) and the S&P 500 (market `US`, quoted in dollars). `lse_stock_analysis/sector_map.json` is the single source of truth: it gives each stock one **Primary Sector** from the scout's 31 sectors (plus optional Secondary Sectors that are recorded but not used). FTSE stocks were mapped by ICB sector; S&P 500 stocks by GICS sub-industry with a few manual overrides (`sp500_map.py`), and **US stocks are never mapped into the three UK-specific sectors**. Stocks that fit no sector (investment trusts, social media, autos, regional banks, and so on) are unmapped and never selected. See [ADR 0002](docs/adr/0002-universe-includes-the-sp500.md).

After an index review run `python -m lse_stock_analysis.universe_check` to list joiners and leavers (it prints a suggested entry for each S&P 500 joiner).

### Prices

One batched Yahoo Finance download for the whole Universe (about 6 months of daily bars, `auto_adjust=True`). Each stock is cut off at its own market's last **completed** session (London or New York, from 17:00 local), so a mid-week retry sees the same data as the Saturday run. Two data problems are handled: Yahoo's pence/pounds switches in LSE stocks (rescaled), and any remaining single-day move over 40%, which flags a **Price Anomaly** (the stock stays in the snapshot but cannot be a Pick). Stocks with no data, too little history (under 60 bars) or stale prices are reported in the email. The Lambda retries a failed or thin download (under 80% coverage) up to three times.

### Picks

For each Signalled Sector, the **Candidates** are the stocks whose Primary Sector it is, from either market. Each stock is analysed with the trend and risk agents (`lse_stock_analysis/agents/`: SMA/RSI/ATR/Bollinger/MACD and volume, a Swing Setup of breakout, pullback, momentum or none, a Setup Grade, a risk score and a 2%-risk position size). A Candidate is **eligible** when it is tradeable, has Setup Grade A or B, no Price Anomaly and fresh prices. Eligible Candidates compete in one combined ranking across both markets (Setup Grade, then lower risk score, then ticker), and the top few become the Picks:

| Signal confidence | Picks |
|---|---|
| below 0.65 | 1 |
| 0.65 to 0.80 | 2 |
| above 0.80 | 3 |

`Divergent` and `Reddit-Led` signals get at most one Pick. There is no LLM in this step, so a past week can be replayed exactly.

### Universe Snapshots

Every week the Lambda saves `snapshots/<week>/universe.csv`: one row per Universe stock with its market, currency, close, indicators, Swing Setup, Setup Grade, sector, whether it was a Candidate or a Pick, the signal's confidence and convergence type, and the methodology and prompt versions. This is the data the backtest uses. `python stock_selection_lambda/backfill.py --bucket <bucket> [--upload]` rebuilds snapshots for past weeks from the saved scout results (flagged `backfilled`; survivorship bias applies because the Universe is today's constituents).

---

## Part 3: testing the methodology and improving it

A **Forward Return** is a stock's price change 2, 4 and 6 weeks after a snapshot, read from later snapshots, in the stock's own currency. A **Hit** is a stock that beats the equal-weight average Forward Return of its own market over the same window. The **Backtest Report** (`lse_stock_analysis/backtest.py`) compares Picks, Candidates and the whole Universe, overall and per market, and breaks Candidates down by market, confidence band, convergence type, Setup Grade, Swing Setup and methodology and prompt version. Every figure shows its sample size, and anything under 30 stock-weeks or 8 weeks is marked *indicative*.

```bash
python stock_selection_lambda/backtest_report.py --bucket sector-scout-results-544611252144 --html report.html
```

**Every quarter** the `backtest-report` Lambda builds the same report from S3, saves it under `reports/<quarter>/` and emails it. You then ask Claude to "run the methodology review" ([`docs/methodology-review.md`](docs/methodology-review.md), skill `.claude/skills/methodology-review`): it checks which prompts and thresholds the evidence supports changing, with an evidence bar, an out-of-sample check and a limit on how many changes per review, and ends in a **pull request for you to review** or a written "no change". Nothing is changed automatically (about 20 weeks of data would mostly fit noise). Each review is recorded in [`docs/methodology-changelog.md`](docs/methodology-changelog.md).

**Methodology versions:** `METHODOLOGY_VERSION` (`lse_stock_analysis/selection.py`) and `PROMPT_VERSION` (`financial_market_news_analyzer/config.py`) are bumped whenever a rule, threshold or prompt changes, and are stored with every week's results so the weeks before and after a change can be compared.

---

## Running it on AWS

Everything is defined in Terraform (`infra/`): three Lambdas, their roles and 90-day log groups, the three schedules, the results bucket, the verified SES sender and a cost alert. Both Lambda zips are built reproducibly with no Docker:

```bash
python stock_selection_lambda/build_zip.py              # stock selection + quarterly report
python financial_market_news_analyzer/build_zip.py      # the scout
cd infra && terraform plan && terraform apply
```

[`infra/README.md`](infra/README.md) has the full deploy, test and cleanup steps. The OpenRouter key is the one thing created by hand (an SSM SecureString), so it stays out of the Terraform state.

**Cost: effectively zero.** Lambda, Scheduler, S3, SES (sent from Lambda) and CloudWatch logs stay far inside the free tier (the weekly stock-selection run takes about two minutes at under 450 MB), there is no NAT gateway, VPC, database or provisioned capacity, and a $2 monthly budget alert emails you if that ever changes. The AWS account is on the paid plan only so it is never shut down.

---

## Development

Python 3.11 or newer; the Lambdas run on 3.11 (scout) and 3.12 (stock selection and the report).

```bash
pip install -r stock_selection_lambda/requirements.txt boto3 "botocore[crt]" pytest
pip install feedparser requests beautifulsoup4 lxml      # for the scout's tests
python -m pytest tests                                   # about 30 seconds, no network or AWS needed
```

Architecture decisions are in `docs/adr/`; issues are tracked on GitHub.

---

## Disclaimer

Not investment advice. Signals are generated by an LLM pipeline over public news and social media data, and Picks come from simple rules over daily price bars. The backtest has few observations, survivorship bias and no transaction costs. Always validate independently before making trading decisions.
