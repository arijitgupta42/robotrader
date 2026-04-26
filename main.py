import json
import pandas as pd
from datetime import date, timedelta
from model_loader import ModelLoader
from get_stock_data import get_index_data
from data_utils import normalise_stock_data
from agents.risk_scoring_agent import RiskScoringAgent
from agents.trend_analysis_agent import TrendAnalysisAgent
from agents.return_projection_agent import ReturnProjectionAgent

import logging
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CAPITAL          = 10_000.0   # Total account capital (GBP)
RISK_PCT         = 0.02       # Max risk per trade (2%)
INDEX            = "FTSE 250" # Wider universe for swing setups
LOOKBACK_DAYS    = 180        # ~6 months — enough history for all indicators
                               # without stale macro noise dragging down signals

# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    # ---- 1. Data download -----------------------------------------------
    end   = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    stock_data = get_index_data(start, end, index=INDEX)

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

    # ---- 4. Risk scoring (swing-aware, 2% rule) ---------------------------
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

    # ---- 5. Tradeable summary (pre-SLM filter) ----------------------------
    tradeable = {
        ticker: risk
        for ticker, risk in risk_results.items()
        if risk.get("tradeable") and risk.get("status") == "ok"
    }

    print(f"\n{'='*60}")
    print(f"  {len(tradeable)} tradeable setups found in {INDEX}")
    print(f"{'='*60}")

    if tradeable:
        # Quick table sorted by setup quality then risk score
        quality_order = {"A": 0, "B": 1, "C": 2, "none": 3}
        rows = []
        for ticker, r in tradeable.items():
            tr = trend_results.get(ticker, {})
            rows.append({
                "Ticker"    : ticker,
                "Setup"     : r["swing_setup"],
                "Quality"   : r["setup_quality"],
                "Risk"      : r["risk_score"],
                "RSI"       : round(tr.get("rsi", 0), 1),
                "ATR%"      : r["atr_stop_pct"],
                "MaxPos%"   : r["max_position_size_pct"],
                "HalfOff%"  : r["half_position_target_atr"],
            })
        df_screen = pd.DataFrame(rows).sort_values(
            ["Quality", "Risk"], key=lambda s: s.map(quality_order) if s.name == "Quality" else s
        )
        print(df_screen.to_string(index=False))
    else:
        print("No setups passed all filters today.")

    # ---- 6. SLM projections (A/B quality setups only) --------------------
    loader = ModelLoader()
    loader.load()

    return_projection_agent = ReturnProjectionAgent(model=loader, max_new_tokens=350)

    ab_tickers = [
        t for t, r in tradeable.items()
        if r.get("setup_quality") in ("A", "B")
    ]

    projection_results = {}
    for ticker in ab_tickers:
        merged = {**trend_results[ticker], **risk_results[ticker]}
        projection_results[ticker] = return_projection_agent.run(ticker, merged)

    # ---- 7. Final output --------------------------------------------------
    print(f"\n{'='*60}")
    print("  SLM Projections — A/B Quality Setups")
    print(f"{'='*60}")

    for ticker, r in projection_results.items():
        if r["status"] == "ok":
            print(
                f"\n{ticker:<12}  Setup: {tradeable[ticker]['swing_setup']:<10} "
                f"Quality: {tradeable[ticker]['setup_quality']}"
            )
            print(
                f"  1W: {r['projected_return_1w_pct']:>+.1f}%   "
                f"1M: {r['projected_return_1m_pct']:>+.1f}%   "
                f"3M: {r['projected_return_3m_pct']:>+.1f}%"
            )
            print(
                f"  Stop:      -{r['stop_loss_pct']:.1f}%   "
                f"Half-off: +{r['first_target_pct']:.1f}%   "
                f"Target: +{r['final_target_pct']:.1f}%"
            )
            print(
                f"  Hold: {r['suggested_hold_days']}d   "
                f"Conf: {r['confidence']:<6}   "
                f"MaxPos: {tradeable[ticker]['max_position_size_pct']:.1f}% of capital"
            )
            print(f"  Rationale: {r['trade_rationale']}")
