import json
import logging
import re
from agents.base_agent import BaseAgent
from model_loader import ModelLoader

logger = logging.getLogger(__name__)


class ReturnProjectionAgent(BaseAgent):
    """
    Projects realistic 1-2 year return estimates for a single
    stock using the Gemma 4 E2B SLM.

    Takes the combined output of TrendAnalysisAgent and
    RiskScoringAgent as input and reasons over the signals
    to produce conservative forward-looking return estimates
    suited to a low-risk, long-term investor.

    Attributes
    ----------
    name : str
        Human-readable name of the agent, used in logging.
    model : ModelLoader
        Shared SLM instance used for inference.
    max_new_tokens : int
        Maximum tokens the SLM may generate per call.
    """

    name = "ReturnProjectionAgent"

    SYSTEM_PROMPT = """You are a return projection agent for a conservative, \
low-risk retail investor on the London Stock Exchange.

Your ONLY job: given trend and risk signals for a single stock, estimate \
realistic 1 and 2 year return projections.

Rules:
- Be conservative. This investor cannot afford large losses.
- Base projections ONLY on the signals provided. Do not invent external knowledge.
- Negative projections are valid for downtrending or high-risk stocks.
- projected_return_1yr_pct and projected_return_2yr_pct are percentages (e.g. 8.5 means 8.5%)
- hold_months is the minimum months this investor should stay invested to see the projected return
- confidence must be one of: "low", "medium", "high"
- projection_basis is one plain English sentence explaining your reasoning

You must respond ONLY with a valid JSON object. No explanation, no markdown, \
no extra text before or after. Exactly this schema:
{
  "ticker": string,
  "projected_return_1yr_pct": float,
  "projected_return_2yr_pct": float,
  "confidence": "low" | "medium" | "high",
  "hold_months": integer,
  "projection_basis": string,
  "status": "ok"
}"""

    def __init__(self, model: ModelLoader, max_new_tokens: int = 256):
        """
        Parameters
        ----------
        model : ModelLoader
            A loaded ModelLoader instance shared across SLM agents.
        max_new_tokens : int, optional
            Maximum tokens the SLM may generate. Default is 256.
        """
        self.model          = model
        self.max_new_tokens = max_new_tokens

    def _process(self, ticker: str, data: dict) -> dict:
        """
        Generates a return projection for a single ticker.

        Builds a structured prompt from the combined trend and
        risk signals, calls the SLM, parses the JSON response,
        and validates the output schema.

        Parameters
        ----------
        ticker : str
            The stock ticker symbol (e.g. 'III.L').
        data : dict
            Merged output of TrendAnalysisAgent and RiskScoringAgent
            for this ticker.

        Returns
        -------
        dict
            Return projection with keys: ticker,
            projected_return_1yr_pct, projected_return_2yr_pct,
            confidence, hold_months, projection_basis, status.
        """
        prompt = self._build_prompt(ticker, data)

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ]

        raw = self.model.generate(messages, max_new_tokens=self.max_new_tokens)
        result = self._parse_response(raw, ticker)
        return result

    def _build_prompt(self, ticker: str, data: dict) -> str:
        """
        Constructs the user prompt from trend and risk signals.

        Parameters
        ----------
        ticker : str
            The stock ticker symbol.
        data : dict
            Merged trend and risk data for the ticker.

        Returns
        -------
        str
            Formatted prompt string for the SLM.
        """
        return f"""Project the return for this LSE stock:

Ticker            : {ticker}
Trend             : {data.get('trend')}
Momentum          : {data.get('momentum_pct')}%
Annualised Vol    : {data.get('annualised_volatility')}
RSI               : {data.get('rsi')}
SMA20             : {data.get('sma_short')}
SMA50             : {data.get('sma_long')}
Risk Score        : {data.get('risk_score')} / 10
Risk Level        : {data.get('risk_level')}
Volatility Signal : {data.get('vol_signal')}
Momentum Signal   : {data.get('momentum_signal')}
RSI Signal        : {data.get('rsi_signal')}

Respond with the JSON object only."""

    def _parse_response(self, raw: str, ticker: str) -> dict:
        """
        Parses and validates the SLM JSON response.

        Attempts strict JSON parsing first, then falls back to
        regex extraction if the model adds surrounding text.
        Returns a fallback dict if both approaches fail.

        Parameters
        ----------
        raw : str
            Raw string output from the SLM.
        ticker : str
            Ticker symbol used to populate fallback response.

        Returns
        -------
        dict
            Parsed and validated projection dict, or a fallback
            dict with status 'failed' if parsing fails.
        """
        # Attempt 1 — direct parse
        try:
            result = json.loads(raw.strip())
            result["status"] = "ok"
            return result
        except json.JSONDecodeError:
            pass

        # Attempt 2 — extract JSON block from surrounding text
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                result["status"] = "ok"
                return result
            except json.JSONDecodeError:
                pass

        # Fallback
        logger.warning(f"[{self.name}] Failed to parse response for {ticker}")
        logger.debug(f"[{self.name}] Raw response: {raw}")
        return {
            "ticker"                  : ticker,
            "projected_return_1yr_pct": 0.0,
            "projected_return_2yr_pct": 0.0,
            "confidence"              : "low",
            "hold_months"             : 12,
            "projection_basis"        : "Fallback — model output could not be parsed.",
            "status"                  : "failed",
        }