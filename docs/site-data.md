# Website data

The stock-selection Lambda publishes the website's data as JSON under `site/` in the results bucket (`stock_selection_lambda/portfolio_step.py`). The GitHub Pages workflow downloads that folder into the web app's `data/` folder at build time, so the site reads these files from its own origin. This file is the contract between the two: change both sides together.

All money is in pounds (`_gbp`) unless a field says otherwise; prices (`price`, `close`, `entry_price`, `stop_price`, `last_price`) are in the stock's own units, pence for `LSE` and US dollars for `US`. `fx` and `gbpusd` are US dollars per pound. Weeks are ISO labels like `2026-W44`. Nothing published contains news headline or Reddit post text.

## `portfolio.json`

The Paper Portfolio now, and its value every week. Rewritten every week from all the weekly records, so a rebuilt week shows up everywhere.

```jsonc
{
  "updated_at": "2026-10-31T06:12:00+00:00",
  "week": "2026-W44",                  // latest week
  "portfolio_version": "2026-10-31.1",
  "starting_cash": 1000.0,
  "opening_week": "2026-W44",
  "cash": 612.40, "holdings": 386.10, "total": 998.50, "benchmark": 1000.0,
  "positions": [{
    "ticker": "BKG.L", "company": "Berkeley Group", "market": "LSE", "currency": "GBp", "sector": "Housebuilders",
    "quantity": 1.98, "entry_week": "2026-W44", "entry_price": 4040.0, "entry_fx": null,
    "cost_basis_gbp": 80.40, "stop_price": 3798.0, "last_price": 4040.0, "last_fx": null,
    "value_gbp": 80.0, "pnl_gbp": -0.40,
    "weeks_held": 0,                    // since entry
    "weeks_to_max_hold": 6,             // until the 6-week exit (restarts when picked again)
    "sector_absent_weeks": 0,           // the sector-absent exit happens at 2
    "stale": false,                     // no usable price this week: last value kept, cannot be sold
    "fx_stale": false,                  // US position valued at an old exchange rate
    "signal": {"sector": "Housebuilders", "confidence": 0.8, "convergence_type": "Convergent"}
  }],
  "history": [{"week": "2026-W44", "cash": 612.40, "holdings": 386.10, "total": 998.50, "benchmark": 1000.0,
               "trades": 4, "scout_ok": true, "gbpusd": 1.2981}],
  "rules": {"max_position_weight": 0.2, "cash_floor": 0.05, "max_positions": 8, "min_trade_gbp": 25.0,
            "stamp_duty": 0.005, "fx_fee": 0.003, "max_hold_weeks": 6, "sector_absent_weeks": 2}
}
```

## `ledger.json`

Every Trade and every week's notes, oldest first.

```jsonc
{
  "trades": [{
    "week": "2026-W44", "side": "buy",            // "buy" | "sell"
    "ticker": "BKG.L", "company": "Berkeley Group", "market": "LSE", "currency": "GBp", "sector": "Housebuilders",
    "reason": "pick",                              // buy: "pick"; sell: "stop" | "max_hold" | "sector_absent"
    "quantity": 1.98, "price": 4040.0, "fx": null,
    "value_gbp": 80.0, "cost_gbp": 0.40,
    "pnl_gbp": null,                               // sells: proceeds after costs minus what was paid
    "signal": {"sector": "Housebuilders", "confidence": 0.8, "convergence_type": "Convergent"}   // buys only
  }],
  "notes": [{
    "week": "2026-W44",
    "kind": "skipped",                  // "skipped" | "stale_price" | "missing_fx" | "no_trade_week"
    "ticker": "PSN.L",                  // null for week-wide notes
    "reason": "not enough cash",        // "skipped" only: "not enough cash" | "no usable price" | "no exchange rate"
                                        //   | "position limit reached" | "under £25"
    "message": "Pick PSN.L not bought: not enough cash."
  }]
}
```

## `weeks/index.json`

```jsonc
{"updated_at": "2026-10-31T06:12:00+00:00", "weeks": ["2026-W44", "2026-W45"]}   // weeks that have a Run Trace
```

## `weeks/YYYY-Www.json` (the Run Trace)

What each stage of the pipeline did that week. A section is `null` when it was not recorded (for example `scout.trace` for a scout that predates the trace block); the website shows such a stage as "no detail recorded". The Paper Portfolio's actions for the week are the `ledger.json` entries with that `week`.

```jsonc
{
  "week": "2026-W44", "generated_at": "...", "methodology_version": "...", "portfolio_version": "...",
  "scout": {
    "timestamp": "20261031T060012Z", "prompt_version": "...", "models": ["primary", "fallback"],
    "macro": "The scout's macro regime summary.",
    "signals": [{                       // public fields only: no conviction_drivers, no retail_thesis
      "sector": "Housebuilders", "confidence": 0.8, "convergence_type": "Convergent", "bear_case_probability": 0.2,
      "disruption_type": "...", "disruption_strength": 0.6, "time_to_impact_weeks": 3, "source_diversity": 0.7,
      "rationale": "...", "propagation": "...", "invalidation_risk": "...", "convergence_note": "...",
      "key_catalysts": ["..."], "correlated_sectors": ["..."]
    }],
    "trace": null                       // the scout's own trace block when present (see the scout's lambda_handler.py)
  },
  "prices": {
    "requested": 853, "returned": 850, "no_data": 3, "short_history": 0, "stale": 2, "anomalies": 1, "unit_fixed": 4,
    "cutoffs": {"LSE": "2026-10-30", "US": "2026-10-30"},
    "coverage": 0.9965                   // live runs only
  },
  "analysis": {"analysed": 845, "failed": 5, "grades": {"A": 40, "B": 210, "C": 595},
               "setups": {"breakout": 30, "pullback": 80, "momentum": 120, "none": 615}, "tradeable": 260},
  "selection": [{
    "sector": "Housebuilders", "confidence": 0.8, "convergence_type": "Convergent",
    "candidates": 12, "eligible_checked": 12,
    "picks_allowed": 2,                 // null when rebuilt from a stored week
    "picks": [{"ticker": "BKG.L", "company": "Berkeley Group", "market": "LSE", "setup_grade": "A", "risk_score": 3, "close": 4040.0}],
    "runners_up": [{"ticker": "PSN.L", "company": "Persimmon", "reason": "ranked below the pick limit"}]   // empty when rebuilt
  }],
  "email": {"sent": true}              // null when rebuilt from a stored week
}
```
