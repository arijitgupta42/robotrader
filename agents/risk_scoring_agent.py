import pandas as pd
from agents.base_agent import BaseAgent


class RiskScoringAgent(BaseAgent):
    """
    Scores the risk profile of a single stock for a low-risk,
    long-term investor based on trend analysis metrics.

    Operates entirely on the output of TrendAnalysisAgent using
    deterministic, threshold-based scoring. No SLM involved.

    Attributes
    ----------
    name : str
        Human-readable name of the agent, used in logging.
    vol_low : float
        Volatility threshold below which risk is considered low.
    vol_high : float
        Volatility threshold above which risk is considered high.
    mom_positive : float
        Momentum % threshold above which momentum is positive.
    mom_negative : float
        Momentum % threshold below which momentum is negative.
    rsi_oversold : float
        RSI threshold below which a stock is considered oversold.
    rsi_overbought : float
        RSI threshold above which a stock is considered overbought.
    """

    name = "RiskScoringAgent"

    def __init__(
        self,
        vol_low: float = 0.20,
        vol_high: float = 0.30,
        mom_positive: float = 15.0,
        mom_negative: float = -10.0,
        rsi_oversold: float = 35.0,
        rsi_overbought: float = 65.0,
    ):
        """
        Parameters
        ----------
        vol_low : float, optional
            Volatility threshold below which risk is low. Default 0.20.
        vol_high : float, optional
            Volatility threshold above which risk is high. Default 0.30.
        mom_positive : float, optional
            Momentum % above which signal is positive. Default 15.0.
        mom_negative : float, optional
            Momentum % below which signal is negative. Default -10.0.
        rsi_oversold : float, optional
            RSI below which stock is oversold. Default 35.0.
        rsi_overbought : float, optional
            RSI above which stock is overbought. Default 65.0.
        """
        self.vol_low       = vol_low
        self.vol_high      = vol_high
        self.mom_positive  = mom_positive
        self.mom_negative  = mom_negative
        self.rsi_oversold  = rsi_oversold
        self.rsi_overbought = rsi_overbought

    def _process(self, ticker: str, trend: dict) -> dict:
        """
        Computes a risk score for a single ticker.

        Each of the four signals (volatility, momentum, RSI, trend)
        contributes points to a raw score which is then normalised
        to a 1–10 scale and mapped to a risk level.

        Parameters
        ----------
        ticker : str
            The stock ticker symbol (e.g. 'III.L').
        trend : dict
            Output dict from TrendAnalysisAgent.run(). Must contain
            keys: annualised_volatility, momentum_pct, rsi, trend.

        Returns
        -------
        dict
            Risk assessment with the following keys:

            ticker : str
                The stock ticker symbol.
            risk_score : int
                Risk score from 1 (safest) to 10 (riskiest).
            risk_level : str
                Bucketed label: 'low', 'medium', or 'high'.
            vol_signal : str
                Volatility signal: 'low', 'medium', or 'high'.
            momentum_signal : str
                Momentum signal: 'positive', 'neutral', or 'negative'.
            rsi_signal : str
                RSI signal: 'oversold', 'neutral', or 'overbought'.
            trend_signal : str
                Trend signal: 'uptrend', 'sideways', or 'downtrend'.
            status : str
                'ok' on success, 'failed' on error.
        """
        vol      = trend["annualised_volatility"]
        momentum = trend["momentum_pct"]
        rsi      = trend["rsi"]
        trend_dir = trend["trend"]

        vol_signal      = self._score_volatility(vol)
        momentum_signal = self._score_momentum(momentum)
        rsi_signal      = self._score_rsi(rsi)
        trend_signal    = trend_dir

        raw_score = (
            self._vol_points(vol_signal)
            + self._momentum_points(momentum_signal)
            + self._rsi_points(rsi_signal)
            + self._trend_points(trend_signal)
        )

        risk_score = self._normalise_score(raw_score)
        risk_level = self._classify_risk(risk_score)

        return {
            "ticker"          : ticker,
            "risk_score"      : risk_score,
            "risk_level"      : risk_level,
            "vol_signal"      : vol_signal,
            "momentum_signal" : momentum_signal,
            "rsi_signal"      : rsi_signal,
            "trend_signal"    : trend_signal,
            "status"          : "ok",
        }

    def _score_volatility(self, vol: float) -> str:
        """
        Classifies volatility into a risk signal.

        Parameters
        ----------
        vol : float
            Annualised volatility as a decimal.

        Returns
        -------
        str
            'low', 'medium', or 'high'.
        """
        if vol < self.vol_low:
            return "low"
        elif vol < self.vol_high:
            return "medium"
        return "high"

    def _score_momentum(self, momentum: float) -> str:
        """
        Classifies momentum into a directional signal.

        Parameters
        ----------
        momentum : float
            Total price change over the full period in percent.

        Returns
        -------
        str
            'positive', 'neutral', or 'negative'.
        """
        if momentum > self.mom_positive:
            return "positive"
        elif momentum > self.mom_negative:
            return "neutral"
        return "negative"

    def _score_rsi(self, rsi: float) -> str:
        """
        Classifies RSI into an entry timing signal.

        Parameters
        ----------
        rsi : float
            Most recent RSI value (0–100).

        Returns
        -------
        str
            'oversold', 'neutral', or 'overbought'.
        """
        if rsi < self.rsi_oversold:
            return "oversold"
        elif rsi < self.rsi_overbought:
            return "neutral"
        return "overbought"

    def _vol_points(self, signal: str) -> int:
        """
        Maps volatility signal to risk points.

        Parameters
        ----------
        signal : str
            Volatility signal: 'low', 'medium', or 'high'.

        Returns
        -------
        int
            Risk points contributed by volatility (1, 2, or 4).
        """
        return {"low": 1, "medium": 2, "high": 4}[signal]

    def _momentum_points(self, signal: str) -> int:
        """
        Maps momentum signal to risk points.

        Parameters
        ----------
        signal : str
            Momentum signal: 'positive', 'neutral', or 'negative'.

        Returns
        -------
        int
            Risk points contributed by momentum (1, 2, or 3).
        """
        return {"positive": 1, "neutral": 2, "negative": 3}[signal]

    def _rsi_points(self, signal: str) -> int:
        """
        Maps RSI signal to risk points.

        Parameters
        ----------
        signal : str
            RSI signal: 'oversold', 'neutral', or 'overbought'.

        Returns
        -------
        int
            Risk points contributed by RSI (2, 1, or 2).
        """
        return {"oversold": 2, "neutral": 1, "overbought": 2}[signal]

    def _trend_points(self, signal: str) -> int:
        """
        Maps trend direction to risk points.

        Parameters
        ----------
        signal : str
            Trend signal: 'uptrend', 'sideways', or 'downtrend'.

        Returns
        -------
        int
            Risk points contributed by trend (1, 2, or 3).
        """
        return {"uptrend": 1, "sideways": 2, "downtrend": 3}[signal]

    def _normalise_score(self, raw: int) -> int:
        """
        Normalises raw points to a 1–10 risk score.

        Raw score ranges from 4 (all low signals) to 12
        (all high signals), mapped linearly to 1–10.

        Parameters
        ----------
        raw : int
            Sum of all signal points (range 4–12).

        Returns
        -------
        int
            Normalised risk score from 1 (safest) to 10 (riskiest).
        """
        raw_min, raw_max = 4, 12
        normalised = (raw - raw_min) / (raw_max - raw_min)
        return round(1 + normalised * 9)

    def _classify_risk(self, score: int) -> str:
        """
        Buckets a numeric risk score into a risk level label.

        Parameters
        ----------
        score : int
            Normalised risk score from 1–10.

        Returns
        -------
        str
            'low' (1–3), 'medium' (4–6), or 'high' (7–10).
        """
        if score <= 3:
            return "low"
        elif score <= 6:
            return "medium"
        return "high"