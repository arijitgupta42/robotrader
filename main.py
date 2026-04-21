import pandas as pd
from datetime import date
from model_loader import ModelLoader
from get_stock_data import get_index_data
from data_utils import normalise_stock_data
from agents.risk_scoring_agent import RiskScoringAgent
from agents.trend_analysis_agent import TrendAnalysisAgent

import logging
logging.basicConfig(level=logging.INFO)

# if __name__ == "__main__":
#     end = date(date.today().year, 1, 1).strftime("%Y-%m-%d")
#     start = date(date.today().year - 2, 1, 1).strftime("%Y-%m-%d")
#     stock_data = get_index_data(start, end)

#     # Running healthcheck on data
#     for ticker, df in stock_data.items():
#         missing = df.isnull().sum().sum()
#         if missing > 0 or df.shape[1] != 5:
#             print(f"{ticker}: shape={df.shape}, missing={missing}")

#     print("Scan complete")

#     # Normalizing data
#     normalised_data = normalise_stock_data(stock_data)

#     # Running the trend analysis agent
#     trend_analysis_agent = TrendAnalysisAgent()
#     trend_results = {
#         ticker: trend_analysis_agent.run(ticker, df)
#         for ticker, df in normalised_data.items()
#     }

#     # Running the risk scoring agent
#     risk_scoring_agent = RiskScoringAgent()
#     risk_results = {
#         ticker: risk_scoring_agent.run(ticker, trend)
#         for ticker, trend in trend_results.items()
#     }

#     # Print Summary
#     df_risk = pd.DataFrame([
#         r for r in risk_results.values() if r["status"] == "ok"
#     ])

#     loader = ModelLoader(endpoint_id="vbnmoz5c4vgkjm")

#     messages = [
#         {
#             "role": "user",
#             "content": "Reply with the word READY and nothing else."
#         }
#     ]

#     response = loader.generate(messages, max_new_tokens=10)
#     print(f"Model response: {response}")