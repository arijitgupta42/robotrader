# Methodology review

Every three months the `backtest-report` Lambda emails a Backtest Report (see `CONTEXT.md` for Hit, Forward Return and Methodology Version). This is how to turn it into improvements to the prompts and the numeric thresholds. To start one, tell Claude: "run the methodology review". The `methodology-review` skill follows this document.

**Nothing is changed automatically.** A review ends in a pull request that the owner reviews and merges, or in a written "no change", which is a valid outcome.

## Get the report

- The emailed copy, or `reports/YYYY-Qn/backtest.json` in the results bucket.
- Or run it now: `python stock_selection_lambda/backtest_report.py --bucket sector-scout-results-544611252144 --html report.html`.
- Read the previous review's entry in `docs/methodology-changelog.md` first: it says what to look for this time.

## What can be changed

| Lever | Where | Replayable on past weeks? |
|---|---|---|
| Prompts | `financial_market_news_analyzer/llm_analyzer.py`, `reddit_analyzer.py`, `consolidator.py` | No (an LLM, so compare by `prompt_version` in later reports) |
| Sector keywords and the models | `financial_market_news_analyzer/config.py` (`LSE_SECTORS`, `OPENROUTER_MODELS`) | No |
| Pick rules: confidence bands, how many Picks, capped convergence types, eligibility, ranking, runners-up | `lse_stock_analysis/selection.py` (`MID_CONFIDENCE`, `HIGH_CONFIDENCE`, `CAPPED_CONVERGENCE`, `GRADE_RANK`, `_ineligible_reason`, `_rank_key`) | Yes: selection is deterministic, so past snapshots can be re-selected |
| Indicator and risk numbers | `lse_stock_analysis/selection.py` (`TREND_PARAMS`, `RISK_PARAMS`) and the two agents in `lse_stock_analysis/agents/` | Yes, by re-running the analysis on the stored prices (`stock_selection_lambda/backfill.py`) |
| Which stocks belong to which Sector (the Sector Map; for the S&P 500 the GICS mapping and overrides) | `lse_stock_analysis/sector_map.json`, `lse_stock_analysis/sp500_map.py` | Yes: re-select past snapshots with the changed map |
| Data-quality thresholds | `lse_stock_analysis/prices.py` (`PRICE_ANOMALY_MOVE`, `MIN_BARS`, `STALE_DAYS`) | Yes |

## Rules for proposing a change

The data is thin (about 20 weeks at first, a handful of Picks), so the risk is fitting noise. A change is proposed only if all of these hold:

1. **Evidence bar.** The figure it rests on is not marked *indicative* (at least 30 stock-weeks and 8 distinct weeks) and its mean excess return is more than 2 standard errors from zero. Anything weaker goes in the changelog as a *watch item* (what to look at next review), not a change.
2. **It holds out of sample.** Split the weeks in time (earlier 75%, later 25%). The effect must have the same sign in both. For a rule or number change, re-select or re-analyse the past weeks with the proposed value and check the Picks' excess return on the held-out weeks too. Do not pick the value that looks best in the whole history.
3. **There is a reason.** A plausible mechanism (why would breakouts underperform in this setup), not only a pattern.
4. **Small steps, few at a time.** At most two or three changes per review, each a small move (one notch on a threshold, not an optimised value). Change one prompt at a time.
5. **Prompt changes are experiments.** They cannot be replayed, so they are judged only by comparing `prompt_version` buckets in the next report, after at least one full quarter.
6. **Do not move the goalposts.** Never change the definition of a hit, the benchmark, or the evidence bar in the same review as a methodology change.
7. **Report what you could not conclude.** Say plainly what is still too thin to act on.

## What a review produces

1. A short written summary: the headline comparison (Picks vs Candidates vs Universe at 2/4/6 weeks, overall and for each market), the breakdowns that clear the bar, the ones that do not, with sample sizes.
2. For each proposed change: the evidence table, the out-of-sample check, the mechanism, and what the next review should see if it worked.
3. A pull request with the changes, which also:
   - bumps `METHODOLOGY_VERSION` in `lse_stock_analysis/selection.py` and/or `PROMPT_VERSION` in `financial_market_news_analyzer/config.py`;
   - updates or adds tests for changed rules;
   - adds an entry to `docs/methodology-changelog.md` (date, versions, change, evidence, what to look for next time, watch items).
4. The deploy steps (rebuild and update the scout and/or stock-selection Lambda), left to the owner as with any production change.

If nothing clears the bar: no code change, but still add the changelog entry with the watch items, so the next review has context.

## Known limits of the data

Survivorship bias (the Universe is today's FTSE 350 and S&P 500); returns are in each stock's own currency, with no FX conversion; overlapping windows between consecutive weeks; stocks in a sector move together, so stock-weeks are not independent; a few Picks per week; returns across a backfilled and a live snapshot can differ by about a dividend yield; weeks before the scout recorded `convergence_type` are excluded automatically.
