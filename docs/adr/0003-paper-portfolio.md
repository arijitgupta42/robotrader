---
status: accepted
---

# A Paper Portfolio traded weekly by fixed rules, from a fixed £1,000

The Backtest Report says whether Picks beat the Universe on average, but not what following them would have done to real money over time: how much to buy, when to sell, what costs and currency do to the result. From W44 2026 the system runs a **Paper Portfolio**: an imaginary £1,000 traded once a week, entirely from the scout's Sector Signals and the Picks, with no money ever added. Its state is published on the project website, and the weekly email reports its Trades.

## Decisions

- **Rules, not an LLM, make every trade.** The allocator is a deterministic function of last week's state and this week's signals, Picks and prices (`lse_stock_analysis/portfolio.py`). The same inputs always give the same Trades, so a week can be re-run, tested and reviewed like the Pick rules. Its rules carry a `PORTFOLIO_VERSION` and change only through the methodology review.
- **Fills at the Universe Snapshot close.** A Trade is filled at the `close` the week's snapshot holds, the last completed session before the scout ran. A real order placed after the scout's Saturday run would fill on Monday, so this is slightly optimistic; it is stated on the website. It needs no second weekly price fetch and matches how Forward Returns are measured.
- **Fixed capital.** £1,000 cash in W44 2026; every buy is paid for from cash, which only grows through sells. No top-ups, no withdrawals, no leverage, no short selling.
- **Valued in pounds.** LSE prices are converted from pence. US prices are converted with Yahoo's `GBPUSD=X` rate for the snapshot date, so the portfolio carries real currency risk (unlike the backtest, which compares each stock in its own currency).
- **Costs.** 0.5% stamp duty on UK buys and a 0.3% currency fee on every US buy and sell; no commission. Fractional shares are allowed, since £1,000 cannot buy a few whole shares of many US stocks.
- **Sizing.** Each Pick's target value is the risk agent's `max_position_size_pct` × the signal's confidence, of total portfolio value, capped at 20%. At least 5% stays as cash, at most 8 Positions are held, and a buy under £25 is skipped. Sells happen first; buys follow in the Picks' rank order until cash runs out, and the remaining Picks are skipped rather than every buy being scaled down.
- **Exits**, checked at each week's review. A Position is sold when:
  1. the close is at or below its stop, the entry price × (1 − `atr_stop_pct` at entry);
  2. it has been held 6 weeks, the far end of the 2–6 week swing horizon; or
  3. its Primary Sector has been missing from the Sector Signals for 2 weeks in a row (one week is tolerated, since weekly signals are noisy).
  A held stock that is picked again is kept, not topped up, and its 6-week clock restarts. There is no take-profit rule.
- **Bad weeks.** Without a successful scout result there are no Trades; Positions are revalued where prices exist and the Ledger records why. A Position with no price this week keeps its last value, is marked stale, and cannot be sold. Without an exchange rate there are no US Trades that week and US Positions keep their last rate.
- **One state file per week.** `portfolio/weeks/YYYY-Www.json` is built only from the previous week's file and that week's inputs. Re-running a week rewrites its file instead of trading twice; if a later week already exists it is rebuilt after. A week whose previous week has no state is refused (except the opening week).

## Consequences

- Stops are only checked against weekly closes, so a stock can fall well through its stop between reviews. The website says so.
- The portfolio's result mixes stock selection with sizing, costs and currency moves. The Backtest Report remains the place to judge stock selection alone; the Paper Portfolio is judged against the **Benchmark**, £1,000 earning the equal-weight Universe average in pounds.
- With few Picks a week and a £25 floor, the portfolio can hold a lot of cash for weeks at a time. That is part of what it measures.
- The stock-selection Lambda runs the portfolio step and publishes the website's data (amendment to ADR 0001). A failure there must never stop the weekly email.
