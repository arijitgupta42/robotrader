---
status: accepted
---

# The Universe is the FTSE 350 plus the S&P 500, with markets kept apart where they differ

The scout flags global themes (AI infrastructure, semiconductors, cybersecurity) that have few or no stocks in the FTSE 350: in the first 22 weeks 12 had no Picks, and one week's only signal had no stock at all. The Universe is therefore the FTSE 350 (market `LSE`, quoted in pence) plus the S&P 500 (market `US`, quoted in US dollars), 853 stocks.

## Decisions

- **Sector Map**: S&P 500 stocks get a Primary Sector from their GICS sub-industry, mapped onto the scout's existing 31 Sectors (`lse_stock_analysis/sp500_map.py`), with a few manual overrides. US stocks are **never mapped into the UK-specific Sectors** (UK Retail Banks, UK General Retail, UK Telecoms & Broadband), whose signals are driven by UK news; a signal on one of those still picks UK stocks only. The Sector names stay as they are. (This decision first left the scout's prompts UK-framed; they were reworded afterwards to cover both markets, see `docs/methodology-changelog.md`, `PROMPT_VERSION` 2026-10-03.2.)
- **Prices**: one batched download; each stock is cut off at its own market's last completed session (London or New York, from 17:00 local). The pence/pounds unit fix applies to LSE stocks only.
- **Picks**: one combined ranking across markets on the existing rules; the email shows each Pick's market and currency. Position sizing is percentage-based, so it needs no currency conversion.
- **Backtest**: a Hit beats the average Forward Return of its **own market**, returns are in each stock's own currency (no FX conversion), and results are also broken down by market. Mixing a bull market or a currency move into every comparison would hide whether stock selection works.
- **History**: the 22 existing weekly snapshots were rebuilt with the larger Universe so a backtest covers both markets.

## Consequences

- A snapshot has `market` and `currency` columns, and `price_cutoff` is per stock's market. Snapshots taken before this decision have neither column and cover the FTSE 350 only; the backtest treats a missing `market` as `LSE`.
- A week's candidate pool is much larger, but the number of Picks per Sector is unchanged (1 to 3 by confidence), so total Picks stay few. Whether that should scale is a question for the methodology review.
- The S&P 500 changes more often than the FTSE 350; `python -m lse_stock_analysis.universe_check` reports both and suggests entries for S&P joiners.
- Survivorship bias now applies to the S&P 500 list too (it is today's constituents).
