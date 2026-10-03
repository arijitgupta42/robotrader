# Robotrader

A weekly pipeline that finds LSE sectors with bullish swing-trading conviction from news and Reddit, then analyses the stocks inside those sectors to choose which to trade over a 2–6 week horizon.

## Sectors and signals

**Sector**:
One of the LSE sub-sectors in the scout's taxonomy (e.g. Housebuilders, Water), defined in `config.py`.
_Avoid_: market, industry

**Sector Signal**:
The scout's weekly bullish call on one Sector, carrying its confidence, disruption type and convergence type.
_Avoid_: market call, recommendation

**Signalled Sector**:
A Sector that appears in this week's Sector Signals, whatever its confidence or convergence type.
_Avoid_: bullish market

## Stocks

**Universe**:
The FTSE 350 stocks (FTSE 100 plus FTSE 250) the system can analyse and pick from, refreshed after each quarterly index review.
_Avoid_: index list, ticker list

**Sector Map**:
A static lookup that gives each Universe stock one Primary Sector and optionally some Secondary Sectors.
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
A stock whose Forward Return beats the equal-weight average Forward Return of the whole Universe over the same window.
_Avoid_: win, correct call

**Backtest Report**:
The comparison of Picks, Candidates and the whole Universe by Forward Return and hit rate, broken down by confidence band, convergence type, Setup Grade, Swing Setup and methodology and prompt version. Every figure carries its sample size; small samples are marked indicative.

**Methodology Version**:
A label (`METHODOLOGY_VERSION` in `selection.py`, `PROMPT_VERSION` in the scout's `config.py`) bumped whenever a selection rule, numeric threshold or prompt changes, and stored with every week's results so the weeks before and after a change can be compared.
_Avoid_: model version

## Stock analysis

**Swing Setup**:
The price pattern a stock currently shows on daily bars: breakout, pullback, momentum or none.

**Setup Grade**:
The A/B/C quality rating of a stock's Swing Setup, from the risk scoring step.
