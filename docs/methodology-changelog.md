# Methodology changelog

One entry per methodology review (`docs/methodology-review.md`), newest first. Each says which versions were in force, what changed and why, and what the next review should look at.

## 2026-10-03: the scout's prompts stop being LSE-framed (`METHODOLOGY_VERSION` unchanged, `PROMPT_VERSION` 2026-10-03.2)

Follows the S&P 500 change below, which left the prompts UK-framed. A scope change, not a result-driven tuning: the news, Reddit and consolidation prompts now describe a global equity portfolio manager covering the FTSE 350 and the S&P 500 (the Reddit prompt no longer rejects posts without a London link), the macro summary names the Fed as well as the BoE, the disruption descriptions are no longer UK-only, and every sector's keyword list gains US company names and themes. The sector names and the three UK-specific sectors are unchanged. Expect more signals on global sectors and more `Reddit-Led` and `Convergent` signals from US-focused posts; weeks before this entry were scored by the UK-framed prompts, so compare `prompt_version` 2026-10-03.1 against 2026-10-03.2 only with that in mind. Watch: the mix of sectors signalled, how often a UK-specific sector is signalled, and the Pick market mix.

## 2026-10-03: the Universe gains the S&P 500 (`METHODOLOGY_VERSION` 2026-10-03.2, `PROMPT_VERSION` unchanged)

A scope change, not a result-driven tuning (see `docs/adr/0002-universe-includes-the-sp500.md`): 503 US stocks added, never mapped into the UK-specific Sectors; one combined pick ranking; per-market cutoffs; the backtest benchmark becomes per market (a hit beats its own market's average). The 22 historical snapshots are rebuilt with the larger Universe. Weeks before this entry were FTSE-only, so compare `methodology_version` 2026-10-03.1 against 2026-10-03.2 only with that in mind. Watch: whether Picks per week rise, which market the Picks come from, and the per-market hit rates.

## 2026-10-03: baseline (`METHODOLOGY_VERSION` 2026-10-03.1, `PROMPT_VERSION` 2026-10-03.1)

No change. First report over 21 weeks (2026-W20 to W40; W19 excluded as keyword-fallback):

- The 12 Picks so far are far too few to judge (11 of 21 weeks had none).
- Candidates (all stocks in signalled sectors) beat the Universe average slightly at 4 weeks (+1.1% excess, ±1.4, 350 stock-weeks): not distinguishable from zero.
- Watch items, all below the evidence bar: Candidates with a momentum setup did best and breakout worst; Reddit-Led signals outperformed News-Led ones; high-confidence (>0.80) signals did worse than low-confidence ones; Setup Grade A did worst of all grades (3 stock-weeks).
- Pick selection is the weak spot to look at first next time: few Picks per week because the eligibility rules are strict and the sectors often have few or no UK stocks.
