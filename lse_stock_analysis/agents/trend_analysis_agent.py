import numpy as np
import pandas as pd
from agents.base_agent import BaseAgent


class TrendAnalysisAgent(BaseAgent):
    """
    Computes trend, momentum, and swing-specific setup metrics
    for a single stock over a short-term (swing) horizon.

    Operates entirely on OHLCV price data using deterministic
    pandas/numpy calculations. No SLM involved. Produces a
    summary dict consumed by RiskScoringAgent.

    Core signals
    ------------
    - SMA-20 / SMA-50 crossover   → trend direction
    - RSI-14                       → overbought/oversold / pullback timing
    - MACD (12/26/9)               → momentum confirmation
    - ATR-14                       → volatility in price units (used for
                                     stop-loss and target sizing)
    - Bollinger Bands (20, 2σ)     → breakout detection
    - Volume surge                 → confirms breakout / momentum
    - Swing setup classification   → 'breakout', 'pullback', 'momentum',
                                     'none'

    Attributes
    ----------
    name : str
        Human-readable name used in logging.
    sma_short : int
        Lookback for short-term SMA (default 20).
    sma_long : int
        Lookback for long-term SMA (default 50).
    rsi_period : int
        RSI lookback (default 14).
    atr_period : int
        ATR lookback (default 14).
    bb_period : int
        Bollinger Band lookback (default 20).
    bb_std : float
        Bollinger Band standard deviation multiplier (default 2.0).
    macd_fast : int
        MACD fast EMA period (default 12).
    macd_slow : int
        MACD slow EMA period (default 26).
    macd_signal : int
        MACD signal EMA period (default 9).
    sideways_threshold : float
        Min fractional SMA gap to classify trend as up/down (default 0.02).
    volume_surge_mult : float
        Volume multiple over 20-day average to flag a surge (default 1.5).
    pullback_rsi_max : float
        RSI ceiling for a pullback signal in an uptrend (default 45).
    breakout_lookback : int
        Days to look back for a resistance high breakout (default 20).
    """

    name = "TrendAnalysisAgent"

    def __init__(
        self,
        sma_short: int = 20,
        sma_long: int = 50,
        rsi_period: int = 14,
        atr_period: int = 14,
        bb_period: int = 20,
        bb_std: float = 2.0,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        sideways_threshold: float = 0.02,
        volume_surge_mult: float = 1.5,
        pullback_rsi_max: float = 45.0,
        breakout_lookback: int = 20,
    ):
        self.sma_short          = sma_short
        self.sma_long           = sma_long
        self.rsi_period         = rsi_period
        self.atr_period         = atr_period
        self.bb_period          = bb_period
        self.bb_std             = bb_std
        self.macd_fast          = macd_fast
        self.macd_slow          = macd_slow
        self.macd_signal        = macd_signal
        self.sideways_threshold = sideways_threshold
        self.volume_surge_mult  = volume_surge_mult
        self.pullback_rsi_max   = pullback_rsi_max
        self.breakout_lookback  = breakout_lookback

    # ------------------------------------------------------------------
    # Public entry (called by BaseAgent.run)
    # ------------------------------------------------------------------

    def _process(self, ticker: str, df: pd.DataFrame) -> dict:
        """
        Computes all swing trading metrics for a single ticker.

        Parameters
        ----------
        ticker : str
            Stock ticker symbol (e.g. 'HSBA.L').
        df : pd.DataFrame
            Normalised OHLCV DataFrame with a DatetimeIndex.
            Must contain columns: Open, High, Low, Close, Volume.

        Returns
        -------
        dict
            Keys:
            ticker, trend, momentum_pct, annualised_volatility,
            rsi, sma_short, sma_long,
            macd_line, macd_signal_line, macd_histogram,
            atr, atr_pct,
            bb_upper, bb_lower, bb_width, price_vs_bb,
            volume_surge, volume_ratio,
            swing_setup,
            status
        """
        close  = df["Close"]
        high   = df["High"]
        low    = df["Low"]
        volume = df["Volume"]

        sma_s   = self._compute_sma(close, self.sma_short)
        sma_l   = self._compute_sma(close, self.sma_long)
        rsi     = self._compute_rsi(close, self.rsi_period)
        momentum = self._compute_momentum(close)
        vol     = self._compute_volatility(close)
        trend   = self._classify_trend(sma_s, sma_l)

        macd_line, macd_sig, macd_hist = self._compute_macd(close)
        atr, atr_pct                   = self._compute_atr(high, low, close)
        bb_upper, bb_lower, bb_width, price_vs_bb = self._compute_bollinger(close)
        volume_surge, volume_ratio      = self._compute_volume_surge(volume)

        swing_setup = self._classify_swing_setup(
            trend, rsi, macd_hist, price_vs_bb, volume_surge, close
        )

        return {
            "ticker"               : ticker,
            # Core trend
            "trend"                : trend,
            "momentum_pct"         : round(momentum, 4),
            "annualised_volatility": round(vol, 4),
            "rsi"                  : round(rsi, 4),
            "sma_short"            : round(sma_s, 4),
            "sma_long"             : round(sma_l, 4),
            # MACD
            "macd_line"            : round(macd_line, 4),
            "macd_signal_line"     : round(macd_sig, 4),
            "macd_histogram"       : round(macd_hist, 4),
            # ATR (swing stop/target sizing)
            "atr"                  : round(atr, 4),
            "atr_pct"              : round(atr_pct, 4),
            # Bollinger
            "bb_upper"             : round(bb_upper, 4),
            "bb_lower"             : round(bb_lower, 4),
            "bb_width"             : round(bb_width, 4),
            "price_vs_bb"          : price_vs_bb,       # 'above', 'below', 'inside'
            # Volume
            "volume_surge"         : volume_surge,      # bool
            "volume_ratio"         : round(volume_ratio, 4),
            # Swing classification
            "swing_setup"          : swing_setup,
            "status"               : "ok",
        }

    # ------------------------------------------------------------------
    # Indicator calculations
    # ------------------------------------------------------------------

    def _compute_sma(self, close: pd.Series, period: int) -> float:
        return close.rolling(window=period).mean().iloc[-1]

    def _compute_rsi(self, close: pd.Series, period: int) -> float:
        delta    = close.diff()
        gain     = delta.clip(lower=0)
        loss     = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
        rs       = avg_gain / avg_loss.replace(0, np.nan)
        rsi      = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]

    def _compute_momentum(self, close: pd.Series) -> float:
        """Total % change over the lookback window."""
        return ((close.iloc[-1] - close.iloc[0]) / close.iloc[0]) * 100

    def _compute_volatility(self, close: pd.Series) -> float:
        """Annualised volatility from daily log returns."""
        log_returns = np.log(close / close.shift(1)).dropna()
        return log_returns.std() * np.sqrt(252)

    def _classify_trend(self, sma_short: float, sma_long: float) -> str:
        diff = (sma_short - sma_long) / sma_long
        if diff > self.sideways_threshold:
            return "uptrend"
        elif diff < -self.sideways_threshold:
            return "downtrend"
        return "sideways"

    def _compute_macd(self, close: pd.Series) -> tuple[float, float, float]:
        """
        MACD line, signal line, and histogram (all most-recent values).

        Returns
        -------
        tuple[float, float, float]
            (macd_line, signal_line, histogram)
        """
        ema_fast   = close.ewm(span=self.macd_fast,   adjust=False).mean()
        ema_slow   = close.ewm(span=self.macd_slow,   adjust=False).mean()
        macd_line  = ema_fast - ema_slow
        signal     = macd_line.ewm(span=self.macd_signal, adjust=False).mean()
        histogram  = macd_line - signal
        return macd_line.iloc[-1], signal.iloc[-1], histogram.iloc[-1]

    def _compute_atr(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> tuple[float, float]:
        """
        Average True Range (Wilder smoothing) and ATR as % of price.

        ATR is used downstream to size stops and profit targets in
        price units rather than percentages, which is more meaningful
        for swing trades.

        Returns
        -------
        tuple[float, float]
            (atr_price_units, atr_pct_of_last_close)
        """
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr     = tr.ewm(alpha=1 / self.atr_period, min_periods=self.atr_period).mean().iloc[-1]
        atr_pct = (atr / close.iloc[-1]) * 100
        return atr, atr_pct

    def _compute_bollinger(
        self, close: pd.Series
    ) -> tuple[float, float, float, str]:
        """
        Bollinger Bands: upper band, lower band, width, and position.

        Returns
        -------
        tuple[float, float, float, str]
            (upper, lower, width_pct, price_vs_bb)
            where price_vs_bb is 'above', 'below', or 'inside'.
        """
        sma    = close.rolling(self.bb_period).mean()
        std    = close.rolling(self.bb_period).std()
        upper  = (sma + self.bb_std * std).iloc[-1]
        lower  = (sma - self.bb_std * std).iloc[-1]
        mid    = sma.iloc[-1]
        width  = ((upper - lower) / mid) * 100  # as % of mid

        last = close.iloc[-1]
        if last > upper:
            pos = "above"
        elif last < lower:
            pos = "below"
        else:
            pos = "inside"

        return upper, lower, width, pos

    def _compute_volume_surge(
        self, volume: pd.Series
    ) -> tuple[bool, float]:
        """
        Detects whether today's volume is a surge vs the 20-day average.

        A volume surge on a breakout day is a strong confirmation signal.

        Returns
        -------
        tuple[bool, float]
            (is_surge, ratio_vs_20d_avg)
        """
        avg_vol = volume.rolling(20).mean().iloc[-1]
        ratio   = volume.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
        return ratio >= self.volume_surge_mult, ratio

    # ------------------------------------------------------------------
    # Swing setup classification
    # ------------------------------------------------------------------

    def _classify_swing_setup(
        self,
        trend: str,
        rsi: float,
        macd_hist: float,
        price_vs_bb: str,
        volume_surge: bool,
        close: pd.Series,
    ) -> str:
        """
        Classifies the current price action into a swing setup type.

        Priority order (first match wins):
        1. breakout  — price closes above prior N-day high with volume surge
        2. pullback  — uptrend with RSI dip into 35–45 range and MACD
                       histogram still positive (dip within a trend)
        3. momentum  — uptrend, MACD positive, RSI 50–65 (trend continuation)
        4. none      — no compelling setup

        Parameters
        ----------
        trend : str
        rsi : float
        macd_hist : float
        price_vs_bb : str
        volume_surge : bool
        close : pd.Series

        Returns
        -------
        str
            'breakout', 'pullback', 'momentum', or 'none'
        """
        last_price      = close.iloc[-1]
        prior_high      = close.iloc[-(self.breakout_lookback + 1):-1].max()
        broke_out       = last_price > prior_high

        if broke_out and volume_surge:
            return "breakout"

        if trend == "uptrend" and (self.pullback_rsi_max - 10) <= rsi <= self.pullback_rsi_max and macd_hist > 0:
            return "pullback"

        if trend == "uptrend" and macd_hist > 0 and 50 <= rsi <= 65:
            return "momentum"

        return "none"
