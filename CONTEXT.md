# Robotrader

A weekly pipeline that finds sectors with bullish swing-trading conviction from news and Reddit, then analyses the stocks inside those sectors to choose which to trade over a 2–6 week horizon.

## Sectors and signals

**Sector**:
One of the 31 sub-sectors in the scout's taxonomy (e.g. Housebuilders, Water), defined in `config.py` and shared by both Markets; three (UK Retail Banks, UK General Retail, UK Telecoms & Broadband) are UK-specific.
_Avoid_: market, industry

**Sector Signal**:
The scout's weekly bullish call on one Sector, carrying its confidence, disruption type and convergence type.
_Avoid_: market call, recommendation

**Signalled Sector**:
A Sector that appears in this week's Sector Signals, whatever its confidence or convergence type.
_Avoid_: bullish market

## Stocks

**Universe**:
The FTSE 350 stocks (FTSE 100 plus FTSE 250) and the S&P 500 stocks the system can analyse and pick from, refreshed after each quarterly index review. See `docs/adr/0002-universe-includes-the-sp500.md`.
_Avoid_: index list, ticker list

**Market**:
`LSE` (the FTSE 350, prices in pence) or `US` (the S&P 500, prices in US dollars). Cutoffs, the pence/pounds fix and the backtest benchmark are per Market.

**Sector Map**:
A static lookup that gives each Universe stock one Primary Sector and optionally some Secondary Sectors. US stocks are never mapped into the UK-specific Sectors.
_Avoid_: classification dictionary, market mapping

**Primary Sector**:
The single Sector a stock belongs to for selection purposes.

**Secondary Sector**:
An additional Sector a stock is exposed to, recorded for later analysis but not used for selection.

**Unmapped Stock**:
A Universe stock that fits no Sector (e.g. an investment trust); it is never selected.

**Uninvestable Sector**:
A Sector that no Universe stock has as its Primary Sector, so a Sector Signal on it yields no stocks.

**Candidate**:
A Universe stock whose Primary Sector is a Signalled Sector in a given week.

**Pick**:
A Candidate chosen for trading in a given week by the rule-based selection.
_Avoid_: recommendation, buy, investment

**Price Anomaly**:
A single-day price move in a stock's recent history large enough to make its indicators untrustworthy; a stock with one cannot be a Pick.
_Avoid_: bad data, glitch

## Backtesting

**Universe Snapshot**:
The stored weekly record of every Universe stock's closing price, indicators, Swing Setup, Setup Grade, Sector and whether it was a Candidate or Pick.
_Avoid_: cache, outcome stub

**Forward Return**:
A stock's price change over 2, 4 or 6 weeks after a Universe Snapshot, read from later snapshots.
_Avoid_: outcome, realised return

**Hit**:
A stock whose Forward Return beats the equal-weight average Forward Return of its own Market over the same window.
_Avoid_: win, correct call

**Backtest Report**:
The comparison of Picks, Candidates and the whole Universe by Forward Return and hit rate, broken down by confidence band, convergence type, Setup Grade, Swing Setup and methodology and prompt version. Every figure carries its sample size; small samples are marked indicative.

**Methodology Version**:
A label (`METHODOLOGY_VERSION` in `selection.py`, `PROMPT_VERSION` in the scout's `config.py`, `PORTFOLIO_VERSION` in `portfolio.py`) bumped whenever a selection rule, portfolio rule, numeric threshold or prompt changes, and stored with every week's results so the weeks before and after a change can be compared.
_Avoid_: model version

## Stock analysis

**Swing Setup**:
The price pattern a stock currently shows on daily bars: breakout, pullback, momentum or none.

**Setup Grade**:
The A/B/C quality rating of a stock's Swing Setup, from the risk scoring step.

## Paper Portfolio

**Paper Portfolio**:
An imaginary portfolio that started with £1,000 cash in W44 2026 and is traded once a week by fixed rules applied to the Sector Signals and Picks, valued in pounds. No money is ever added or withdrawn. See `docs/adr/0003-paper-portfolio.md`.
_Avoid_: fund, account, live portfolio

**Position**:
A holding of one stock in the Paper Portfolio: how many shares (fractions allowed), the price, exchange rate and week it was bought at, its stop price, and how long it has been held.
_Avoid_: holding, investment

**Trade**:
One simulated buy or sell of a Position, filled at the week's Universe Snapshot close, with its cost and the reason for it (a Pick, a stop, the holding limit, or the Sector no longer being signalled). A Pick is not a Trade: a Pick can be skipped when cash runs out, the trade would be too small, or the Position limit is reached.
_Avoid_: order, transaction, execution

**Ledger**:
The full record of every Trade and every week's notes (no-trade weeks, stale prices, a missing exchange rate, skipped Picks and why).
_Avoid_: trade log, history

**Benchmark**:
What £1,000 would be worth from W44 2026 if it earned the equal-weight average weekly return of the whole Universe, in pounds. The Paper Portfolio is judged against it.
_Avoid_: index, market return

**Run Trace**:
The weekly record of what each stage of the pipeline did (feeds fetched, Reddit requests, which model answered, Sector Signals, Candidates, Picks and rejections, prices, the Paper Portfolio's Trades), published for the website. It holds counts, names and the scout's own reasoning, never news headline or Reddit post text.
_Avoid_: log, debug output
