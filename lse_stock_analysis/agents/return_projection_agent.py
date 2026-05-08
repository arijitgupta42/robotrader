import json
import logging
import re
from agents.base_agent import BaseAgent
from model_loader import ModelLoader

logger = logging.getLogger(__name__)


class ReturnProjectionAgent(BaseAgent):
    """
    Projects short-term return estimates for a swing trade candidate
    using the Gemma 4 E2B SLM.

    Takes the combined output of TrendAnalysisAgent and RiskScoringAgent
    and produces 1-week, 1-month, and 3-month return projections along
    with concrete trade management levels.

    Framing: the user is a *swing investor* — they will never trade a
    company they don't believe in long-term, they take half the position
    off at the first target to guarantee a break-even trade, and they
    never risk more than 2% of capital per trade.

    Attributes
    ----------
    name : str
    model : ModelLoader
    max_new_tokens : int
    """

    name = "ReturnProjectionAgent"

    SYSTEM_PROMPT = """You are a swing trade projection agent for a disciplined retail swing investor
on the London Stock Exchange (FTSE 250).

INVESTOR PROFILE:
- Never trades a company they don't believe in long-term.
- Holds for 5–20 trading days.
- Always takes HALF the position off at the first target to guarantee a break-even trade.
- Never risks more than 2% of total capital per trade.
- Targets moves of 10% or more. Will skip a trade if the setup doesn't offer that potential.
- Focuses on: breakouts, RSI pullbacks in uptrends, and strong MACD momentum.

YOUR JOB:
Given trend, momentum, and risk signals for a single FTSE 250 stock, produce:
1. Short-term return projections (1 week, 1 month, 3 months).
2. A suggested stop-loss distance (as % below entry, based on ATR).
3. A first target (half-off level, as % above entry).
4. A final target (full exit level, as % above entry).
5. Confidence and reasoning.

RULES:
- Be realistic. Only project 10%+ if the setup genuinely supports it.
- Projections are % moves from current price (positive = up, negative = down).
- stop_loss_pct is a positive number representing % below entry (e.g. 3.0 means stop 3% below entry).
- first_target_pct is where the investor takes half off (should be achievable in 1–5 days).
- final_target_pct is the full swing target (should be achievable in the full hold period).
- confidence must be one of: "low", "medium", "high".
- trade_rationale is one plain English sentence — why this setup is (or isn't) worth taking.
- If the setup is 'none' or the trend is not 'uptrend', projections should be flat or negative.

Respond ONLY with a valid JSON object. No preamble, no markdown, no extra text.
Exact schema:
{
  "ticker": string,
  "projected_return_1w_pct": float,
  "projected_return_1m_pct": float,
  "projected_return_3m_pct": float,
  "stop_loss_pct": float,
  "first_target_pct": float,
  "final_target_pct": float,
  "suggested_hold_days": integer,
  "confidence": "low" | "medium" | "high",
  "trade_rationale": string,
  "status": "ok"
}"""

    def __init__(self, model: ModelLoader, max_new_tokens: int = 350):
        self.model          = model
        self.max_new_tokens = max_new_tokens

    def _process(self, ticker: str, data: dict) -> dict:
        """
        Generate a swing trade projection for one ticker.

        Parameters
        ----------
        ticker : str
        data : dict
            Merged output of TrendAnalysisAgent and RiskScoringAgent.

        Returns
        -------
        dict
            Projection with 1W/1M/3M returns, stop, targets, rationale.
        """
        prompt = self._build_prompt(ticker, data)

        messages = [
            {"role": "system", "content": [{"type": "text", "text": self.SYSTEM_PROMPT}]},
            {"role": "user",   "content": [{"type": "text", "text": prompt}]},
        ]

        raw    = self.model.generate(messages, max_new_tokens=self.max_new_tokens)
        result = self._parse_response(raw, ticker)
        return result

    def _build_prompt(self, ticker: str, data: dict) -> str:
        return f"""Swing trade projection for this FTSE 250 stock:

Ticker              : {ticker}
Trend               : {data.get('trend')}
Swing Setup         : {data.get('swing_setup')}
Setup Quality       : {data.get('setup_quality')}
Momentum %          : {data.get('momentum_pct')}%
RSI                 : {data.get('rsi')}
MACD Histogram      : {data.get('macd_histogram')}
ATR %               : {data.get('atr_pct')}%
ATR Stop (1.5×ATR)  : {data.get('atr_stop_pct')}%
Price vs BB         : {data.get('price_vs_bb')}
BB Width            : {data.get('bb_width')}%
Volume Surge        : {data.get('volume_surge')}
Volume Ratio        : {data.get('volume_ratio')}×
SMA20               : {data.get('sma_short')}
SMA50               : {data.get('sma_long')}
Risk Score          : {data.get('risk_score')} / 10
Risk Level          : {data.get('risk_level')}
Tradeable           : {data.get('tradeable')}
Max Position Size   : {data.get('max_position_size_pct')}% of capital
Half-off Target     : {data.get('half_position_target_atr')}% above entry

Respond with the JSON object only."""

    def _parse_response(self, raw: str, ticker: str) -> dict:
        """
        Parse and validate the SLM's JSON response.

        Falls back gracefully if the model adds surrounding text or
        fails to produce valid JSON.
        """
        try:
            result = json.loads(raw.strip())
            result["status"] = "ok"
            return result
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                result["status"] = "ok"
                return result
            except json.JSONDecodeError:
                pass

        logger.warning(f"[{self.name}] Failed to parse response for {ticker}")
        logger.debug(f"[{self.name}] Raw: {raw}")
        return {
            "ticker"                  : ticker,
            "projected_return_1w_pct" : 0.0,
            "projected_return_1m_pct" : 0.0,
            "projected_return_3m_pct" : 0.0,
            "stop_loss_pct"           : 0.0,
            "first_target_pct"        : 0.0,
            "final_target_pct"        : 0.0,
            "suggested_hold_days"     : 10,
            "confidence"              : "low",
            "trade_rationale"         : "Fallback — model output could not be parsed.",
            "status"                  : "failed",
        }
