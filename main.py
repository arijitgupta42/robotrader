import os
import logging
import pandas as pd
from datetime import date, timedelta

from model_loader import ModelLoader
from get_stock_data import get_index_data, save_data, load_data
from data_utils import normalise_stock_data
from agents.risk_scoring_agent import RiskScoringAgent
from agents.trend_analysis_agent import TrendAnalysisAgent
from agents.return_projection_agent import ReturnProjectionAgent

logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CAPITAL       = 1000.0    # Total account capital (GBP)
RISK_PCT      = 0.02        # Max risk per trade (2%)
INDEX         = 'FTSE 250'
LOOKBACK_DAYS = 180         # ~6 months of daily bars

# Set to a filename to cache today's raw download and skip re-downloading
# on subsequent runs.  Set to None to always download fresh.
CACHE_FILE    = 'data_cache.csv'

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    # ---- 1. Data acquisition ----------------------------------------------
    end   = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    if CACHE_FILE and os.path.exists(CACHE_FILE):
        logging.info(f"Loading cached data from {CACHE_FILE}")
        stock_data = load_data(CACHE_FILE)
    else:
        stock_data = get_index_data(start, end, index=INDEX)
        if CACHE_FILE:
            save_data(stock_data, CACHE_FILE)

    # ---- 2. Normalise ------------------------------------------------------
    normalised_data = normalise_stock_data(stock_data)

    # ---- 3. Trend analysis (swing metrics) --------------------------------
    trend_analysis_agent = TrendAnalysisAgent(
        sma_short          = 20,
        sma_long           = 50,
        rsi_period         = 14,
        atr_period         = 14,
        bb_period          = 20,
        macd_fast          = 12,
        macd_slow          = 26,
        macd_signal        = 9,
        sideways_threshold = 0.02,
        volume_surge_mult  = 1.5,
        pullback_rsi_max   = 45.0,
        breakout_lookback  = 20,
    )
    trend_results = {
        ticker: trend_analysis_agent.run(ticker, df)
        for ticker, df in normalised_data.items()
    }

    # ---- 4. Risk scoring --------------------------------------------------
    risk_scoring_agent = RiskScoringAgent(
        capital        = CAPITAL,
        risk_pct       = RISK_PCT,
        vol_low        = 1.5,
        vol_high       = 4.0,
        rsi_oversold   = 30.0,
        rsi_overbought = 70.0,
    )
    risk_results = {
        ticker: risk_scoring_agent.run(ticker, trend)
        for ticker, trend in trend_results.items()
    }

    # ---- 5. Screening table (pre-SLM) -------------------------------------
    tradeable = {
        ticker: risk
        for ticker, risk in risk_results.items()
        if risk.get("tradeable") and risk.get("status") == "ok"
    }

    print(f"\n{'='*62}")
    print(f"  {len(tradeable)} tradeable setups in {INDEX}")
    print(f"{'='*62}")

    if tradeable:
        quality_order = {"A": 0, "B": 1, "C": 2, "none": 3}
        rows = []
        for ticker, r in tradeable.items():
            tr = trend_results.get(ticker, {})
            rows.append({
                "Ticker"   : ticker,
                "Setup"    : r["swing_setup"],
                "Grade"    : r["setup_quality"],
                "Risk"     : r["risk_score"],
                "RSI"      : round(tr.get("rsi", 0), 1),
                "ATR%"     : round(r["atr_stop_pct"], 2),
                "MaxPos%"  : round(r["max_position_size_pct"], 1),
                "HalfOff%" : round(r["half_position_target_atr"], 2),
            })
        df_screen = pd.DataFrame(rows).sort_values(
            ["Grade", "Risk"],
            key=lambda s: s.map(quality_order) if s.name == "Grade" else s
        )
        print(df_screen.to_string(index=False))
    else:
        print("No setups passed all filters today.")

    # ---- 6. SLM projections (A/B grade only) ------------------------------
    loader = ModelLoader()
    loader.load()

    ab_tickers = [
        t for t, r in tradeable.items()
        if r.get("setup_quality") in ("A", "B")
    ]

    projection_results = {}
    for ticker in ab_tickers:
        merged = {**trend_results[ticker], **risk_results[ticker]}
        projection_results[ticker] = ReturnProjectionAgent(
            model=loader, max_new_tokens=350
        ).run(ticker, merged)

    # ---- 7. Final output --------------------------------------------------
    print(f"\n{'='*62}")
    print("  SLM projections — A/B grade setups")
    print(f"{'='*62}")

    for ticker, r in projection_results.items():
        if r["status"] == "ok":
            g = tradeable[ticker]
            print(
                f"\n{ticker:<12} {g['swing_setup']:<10} grade={g['setup_quality']}  "
                f"risk={g['risk_score']}/10"
            )
            print(
                f"  Returns  1W {r['projected_return_1w_pct']:>+.1f}%  "
                f"1M {r['projected_return_1m_pct']:>+.1f}%  "
                f"3M {r['projected_return_3m_pct']:>+.1f}%"
            )
            print(
                f"  Levels   stop −{r['stop_loss_pct']:.1f}%  "
                f"half-off +{r['first_target_pct']:.1f}%  "
                f"target +{r['final_target_pct']:.1f}%"
            )
            print(
                f"  Hold {r['suggested_hold_days']}d   conf={r['confidence']}   "
                f"max position {g['max_position_size_pct']:.1f}% of capital"
            )
            print(f"  {r['trade_rationale']}")