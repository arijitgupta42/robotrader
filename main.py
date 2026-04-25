import json
import pandas as pd
from datetime import date
from model_loader import ModelLoader
from get_stock_data import get_index_data
from data_utils import normalise_stock_data
from agents.risk_scoring_agent import RiskScoringAgent
from agents.trend_analysis_agent import TrendAnalysisAgent
from agents.return_projection_agent import ReturnProjectionAgent

import logging
logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    end = date(date.today().year, 1, 1).strftime("%Y-%m-%d")
    start = date(date.today().year - 2, 1, 1).strftime("%Y-%m-%d")
    stock_data = get_index_data(start, end)

    # Running healthcheck on data
    for ticker, df in stock_data.items():
        missing = df.isnull().sum().sum()
        if missing > 0 or df.shape[1] != 5:
            print(f"{ticker}: shape={df.shape}, missing={missing}")

    print("Scan complete")

    # Normalizing data
    normalised_data = normalise_stock_data(stock_data)

    # Running the trend analysis agent
    trend_analysis_agent = TrendAnalysisAgent()
    trend_results = {
        ticker: trend_analysis_agent.run(ticker, df)
        for ticker, df in normalised_data.items()
    }

    # Running the risk scoring agent
    risk_scoring_agent = RiskScoringAgent()
    risk_results = {
        ticker: risk_scoring_agent.run(ticker, trend)
        for ticker, trend in trend_results.items()
    }

    # Print Summary
    df_risk = pd.DataFrame([
        r for r in risk_results.values() if r["status"] == "ok"
    ])

    # Load language model
    loader = ModelLoader()
    loader.load()

    # Identifying low risk tickers and running return porjection model
    low_risk_tickers = [
        t for t, r in risk_results.items()
        if r.get("risk_level") == "low"
    ]

    return_projection_agent = ReturnProjectionAgent(model=loader)

    projection_results = {}
    for ticker in low_risk_tickers:
        merged = {**trend_results[ticker], **risk_results[ticker]}
        projection_results[ticker] = return_projection_agent.run(ticker, merged)

    # Quick summary
    for ticker, r in projection_results.items():
        if r["status"] == "ok":
            print(f"{ticker:<12} 1yr={r['projected_return_1yr_pct']:>6.1f}%  "
                f"2yr={r['projected_return_2yr_pct']:>6.1f}%  "
                f"conf={r['confidence']:<6}  hold={r['hold_months']}m")