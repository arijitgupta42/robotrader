# LSE Sector Scout

A three-pass LLM pipeline that identifies LSE sub-sectors likely to show positive price movement over a **2–6 week swing horizon**, driven by structural disruptions rather than daily noise.

---

## How It Works

```
RSS feeds + HL scrape ──► LLM Pass 1 (news)   ─┐
                                                 ├─► LLM Pass 3 (consolidate) ─► ConsolidatedSignals
Reddit RSS             ──► LLM Pass 2 (reddit) ─┘
```

**Pass 1 — News analysis** (`llm_analyzer.py`)  
Scrapes RSS feeds and the HL Weekly Outlook, then sends headlines to an LLM instructed to act as a senior LSE portfolio manager. Returns structured signals with sector, confidence, disruption type, propagation mechanism, and conviction drivers.

**Pass 2 — Reddit sentiment** (`reddit_analyzer.py`)  
Pulls posts from finance/trading subreddits via RSS (no API key required). A separate LLM pass extracts genuine bullish retail conviction signals, graded by quality (DD, Discussion, News, Meme, Mixed).

**Pass 3 — Consolidation** (`consolidator.py`)  
Merges both inputs and labels each signal as one of:
- `Convergent` — news thesis and retail conviction agree → confidence boosted up to +0.10
- `News-Led` — strong news thesis, Reddit absent or neutral
- `Reddit-Led` — strong retail DD with no news confirmation (haircut applied)
- `Divergent` — news bullish, Reddit explicitly bearish → confidence discounted

---

## Installation

```bash
pip install feedparser requests beautifulsoup4 boto3
```

> `boto3` is only required if you're loading the OpenRouter API key and model list from AWS SSM. For local use, see **Configuration** below.

---

## Configuration

### API Key

Set your [OpenRouter](https://openrouter.ai) API key as an environment variable:

```bash
export OPENROUTER_API_KEY=sk-or-...
```

If `boto3` is available, the key is also looked up from SSM at `/sector-scout/openrouter-api-key` first.

### Models

The default model list is in `config.py` under `OPENROUTER_MODELS`. These are free-tier models on OpenRouter — swap them out for anything supported by the API. The pipeline chunks models in groups of three and uses OpenRouter's native fallback (`route: "fallback"`) within each chunk.

### Thresholds

In `config.py`:

```python
SCHEDULER_CFG.min_confidence          = 0.55   # minimum confidence to pass to consolidator
SCHEDULER_CFG.min_disruption_strength = 0.50   # minimum disruption strength
REDDIT_CFG.min_reddit_conviction      = 0.40   # minimum bullish_conviction from Reddit pass
SCHEDULER_CFG.max_headlines_per_cycle = 500
REDDIT_CFG.max_posts_per_cycle        = 200
```

---

## Usage

### One-shot run

```bash
python sector_scout.py
```

News-only mode (faster, skips Reddit):

```bash
python sector_scout.py --skip-reddit
```

Verbose mode (prints intermediate signal counts for all three passes):

```bash
python sector_scout.py --verbose
```

### From your own code

```python
from sector_scout import SectorScout

scout = SectorScout()
signals, llm_succeeded = scout.run_cycle()

# Filter to high-conviction convergent signals
positions = {
    sig.sector: sig.confidence
    for sig in signals
    if sig.confidence >= 0.65
    and sig.disruption_strength >= 0.55
    and sig.convergence_type in ("Convergent", "News-Led")
}
```

`run_cycle()` returns `(List[ConsolidatedSignal], bool)`. The boolean is `True` only when the consolidation LLM call itself succeeded — treat `False` as a pipeline failure even if signals are present (they'll be raw news signals wrapped as fallback output).

For debugging, `run_cycle_verbose()` returns a dict with all intermediate results:

```python
result = scout.run_cycle_verbose()
# result.keys(): signals, llm_succeeded, news_signals, reddit_signals, macro_regime
```

---

## ConsolidatedSignal Fields

| Field | Type | Description |
|---|---|---|
| `sector` | `str` | LSE sub-sector name (exact from taxonomy) |
| `confidence` | `float` | 0–1, consolidated LLM conviction |
| `disruption_type` | `str` | Disruption category (see taxonomy below) |
| `disruption_strength` | `float` | 0–1, magnitude of structural break |
| `time_to_impact_weeks` | `int` | Estimated weeks before full market pricing |
| `bear_case_probability` | `float` | Probability of negative/flat outcome |
| `convergence_type` | `str` | `Convergent` \| `News-Led` \| `Reddit-Led` \| `Divergent` |
| `convergence_note` | `str` | Why news and Reddit agree or disagree |
| `propagation` | `str` | Mechanism sentence |
| `invalidation_risk` | `str` | What kills the thesis, with probability |
| `rationale` | `str` | Consolidated investment case |
| `retail_thesis` | `str` | What the Reddit crowd believes |
| `key_catalysts` | `list` | Upcoming confirming events |
| `correlated_sectors` | `list` | Secondary sector plays (0–2) |
| `conviction_drivers` | `list` | Evidence items tagged `[Source: News\|Reddit]` |
| `macro_regime_summary` | `str` | Macro backdrop at time of analysis |
| `timestamp` | `datetime` | UTC timestamp |

---

## Sector & Disruption Taxonomy

The full sector list (35 LSE sub-sectors) and disruption category definitions live in `config.py`. Sectors span Technology, Financials, Energy, Healthcare, Consumer, Industrials, Materials, Real Estate, Utilities, and Telecoms. The eight disruption types are:

- Supply Chain Dislocation
- Regulatory / Policy Shift
- Macro Regime Change
- Geopolitical Shock
- Technology / Adoption Inflection
- Earnings / Guidance Divergence
- Commodity Price Inflection
- M&A / Consolidation Wave

---

## Data Sources

**News:** 20+ RSS feeds including BBC Business, Guardian, City A.M., Sky News, AP, Yahoo Finance, Bank of England, Investing.com, Investegate RNS, and Proactive Investors. Also scrapes the HL Weekly Outlook article each week.

**Reddit:** UKInvesting, UKPersonalFinance, investing, stocks, SecurityAnalysis, Economics, Commodities, energy, wallstreetbets, options, thetagang — all via public RSS (no authentication required).

---

## Failure Modes

| Scenario | Behaviour |
|---|---|
| News LLM fails | Keyword heuristic fallback used; `llm_succeeded=False` |
| Reddit unreachable | Pipeline continues with news-only input |
| Reddit LLM fails | Consolidator runs with no Reddit signals |
| Consolidator fails | Raw news signals wrapped as `News-Led` ConsolidatedSignals; `llm_succeeded=False` |
| All passes fail | Returns `([], False)` |

---

## Cron Example

```bash
# Every Sunday at 07:00
0 7 * * 0 /path/to/venv/bin/python /path/to/sector_scout.py >> /var/log/sector_scout.log 2>&1
```

---

## Disclaimer

Not investment advice. Signals are generated by an LLM pipeline over public news and social media data. Always validate independently before making trading decisions.
