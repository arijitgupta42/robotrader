import numpy as np
import pandas as pd
from agents.base_agent import BaseAgent


class TrendAnalysisAgent(BaseAgent):
    """
    Computes trend and momentum metrics for a single stock.

    Operates entirely on OHLCV price data using deterministic
    pandas/numpy calculations. No SLM involved. Produces a
    summary dict consumed by the RiskScoringAgent.

    Attributes
    ----------
    name : str
        Human-readable name of the agent, used in logging.
    sma_short : int
        Lookback period for the short-term moving average.
    sma_long : int
        Lookback period for the long-term moving average.
    rsi_period : int
        Lookback period for the RSI calculation.
    sideways_threshold : float
        Minimum % difference between SMA-20 and SMA-50
        required to classify a trend as up or down.
        Below this it is classified as sideways.
    """

    name = "TrendAnalysisAgent"

    def __init__(
        self,
        sma_short: int = 20,
        sma_long: int = 50,
        rsi_period: int = 14,
        sideways_threshold: float = 0.02,
    ):
        """
        Parameters
        ----------
        sma_short : int, optional
            Lookback period for the short-term moving average.
            Default is 20.
        sma_long : int, optional
            Lookback period for the long-term moving average.
            Default is 50.
        rsi_period : int, optional
            Lookback period for the RSI calculation.
            Default is 14.
        sideways_threshold : float, optional
            Minimum fractional difference between SMA-20 and SMA-50
            to classify a trend as up or down. Default is 0.02 (2%).
        """
        self.sma_short = sma_short
        self.sma_long = sma_long
        self.rsi_period = rsi_period
        self.sideways_threshold = sideways_threshold

    def _process(self, ticker: str, df: pd.DataFrame) -> dict:
        """
        Computes trend metrics for a single ticker.

        Parameters
        ----------
        ticker : str
            The stock ticker symbol (e.g. 'III.L').
        df : pd.DataFrame
            Normalised OHLCV DataFrame for the ticker.
            Must contain a 'Close' column with a DatetimeIndex.

        Returns
        -------
        dict
            Summary metrics with the following keys:

            ticker : str
                The stock ticker symbol.
            sma_short : float
                Most recent SMA-20 value.
            sma_long : float
                Most recent SMA-50 value.
            rsi : float
                Most recent RSI-14 value (0–100).
            momentum_pct : float
                Total price change over the full period in percent.
            annualised_volatility : float
                Annualised standard deviation of daily returns.
            trend : str
                Trend direction: 'uptrend', 'downtrend', or 'sideways'.
            status : str
                'ok' on success, 'failed' on error.
        """
        close = df["Close"]

        sma_short = self._compute_sma(close, self.sma_short)
        sma_long  = self._compute_sma(close, self.sma_long)
        rsi       = self._compute_rsi(close, self.rsi_period)
        momentum  = self._compute_momentum(close)
        vol       = self._compute_volatility(close)
        trend     = self._classify_trend(sma_short, sma_long)

        return {
            "ticker"                : ticker,
            "sma_short"             : round(sma_short, 4),
            "sma_long"              : round(sma_long, 4),
            "rsi"                   : round(rsi, 4),
            "momentum_pct"          : round(momentum, 4),
            "annualised_volatility" : round(vol, 4),
            "trend"                 : trend,
            "status"                : "ok",
        }

    def _compute_sma(self, close: pd.Series, period: int) -> float:
        """
        Computes the most recent simple moving average.

        Parameters
        ----------
        close : pd.Series
            Daily closing prices.
        period : int
            Lookback window in trading days.

        Returns
        -------
        float
            Most recent SMA value.
        """
        return close.rolling(window=period).mean().iloc[-1]

    def _compute_rsi(self, close: pd.Series, period: int) -> float:
        """
        Computes the most recent RSI value using Wilder's method.

        Parameters
        ----------
        close : pd.Series
            Daily closing prices.
        period : int
            Lookback window in trading days.

        Returns
        -------
        float
            Most recent RSI value in the range 0–100.
        """
        delta = close.diff()
        gain  = delta.clip(lower=0)
        loss  = -delta.clip(upper=0)

        avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()

        rs  = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]

    def _compute_momentum(self, close: pd.Series) -> float:
        """
        Computes total price change over the full period.

        Parameters
        ----------
        close : pd.Series
            Daily closing prices.

        Returns
        -------
        float
            Percentage price change from first to last close.
        """
        return ((close.iloc[-1] - close.iloc[0]) / close.iloc[0]) * 100

    def _compute_volatility(self, close: pd.Series) -> float:
        """
        Computes annualised volatility from daily log returns.

        Parameters
        ----------
        close : pd.Series
            Daily closing prices.

        Returns
        -------
        float
            Annualised volatility as a decimal (e.g. 0.18 = 18%).
        """
        log_returns = np.log(close / close.shift(1)).dropna()
        return log_returns.std() * np.sqrt(252)

    def _classify_trend(self, sma_short: float, sma_long: float) -> str:
        """
        Classifies trend direction from SMA crossover.

        Parameters
        ----------
        sma_short : float
            Most recent short-term SMA value.
        sma_long : float
            Most recent long-term SMA value.

        Returns
        -------
        str
            'uptrend' if SMA-20 is meaningfully above SMA-50,
            'downtrend' if meaningfully below,
            'sideways' if the difference is within the threshold.
        """
        diff = (sma_short - sma_long) / sma_long
        if diff > self.sideways_threshold:
            return "uptrend"
        elif diff < -self.sideways_threshold:
            return "downtrend"
        return "sideways"