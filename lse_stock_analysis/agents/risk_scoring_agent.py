import pandas as pd
from agents.base_agent import BaseAgent


class RiskScoringAgent(BaseAgent):
    """
    Scores each stock for suitability as a swing trade, applying
    strict 2%-of-capital risk management per position.

    Unlike the previous long-term scoring model this agent:
    - Rejects sideways/downtrend stocks outright (no setup = no trade)
    - Uses ATR to compute stop-loss distance and position size
    - Rewards high-conviction swing setups (breakout, pullback, momentum)
    - Enforces the half-position / break-even rule by sizing stops at
      1×ATR (tight enough that half-off at 1×ATR leaves the remainder
      at break-even or better)
    - Penalises extremely high volatility — a 5%/day mover is hard to
      manage with a 2% capital risk budget

    Attributes
    ----------
    name : str
    capital : float
        Total trading capital in GBP. Used to compute max £ risk per trade.
    risk_pct : float
        Maximum fraction of capital to risk per trade (default 0.02 = 2%).
    vol_low : float
        ATR% below which volatility is considered low for a swing (default 1.5%).
    vol_high : float
        ATR% above which volatility is considered dangerously high (default 4.0%).
    rsi_oversold : float
        RSI floor for a valid swing entry (default 30).
    rsi_overbought : float
        RSI ceiling for a valid swing entry (default 70).
    """

    name = "RiskScoringAgent"

    def __init__(
        self,
        capital: float = 10_000.0,
        risk_pct: float = 0.02,
        vol_low: float = 1.5,
        vol_high: float = 4.0,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
    ):
        """
        Parameters
        ----------
        capital : float
            Total account capital in GBP. Default 10,000.
        risk_pct : float
            Max risk per trade as fraction of capital. Default 0.02 (2%).
        vol_low : float
            ATR% floor for 'low' swing volatility. Default 1.5.
        vol_high : float
            ATR% ceiling before volatility becomes unmanageable. Default 4.0.
        rsi_oversold : float
            RSI below which we avoid entries (momentum too weak). Default 30.
        rsi_overbought : float
            RSI above which we avoid entries (stretched, reversal risk). Default 70.
        """
        self.capital        = capital
        self.risk_pct       = risk_pct
        self.vol_low        = vol_low
        self.vol_high       = vol_high
        self.rsi_oversold   = rsi_oversold
        self.rsi_overbought = rsi_overbought

    # ------------------------------------------------------------------
    # Core processing
    # ------------------------------------------------------------------

    def _process(self, ticker: str, trend: dict) -> dict:
        """
        Scores a single ticker for swing trade suitability.

        Parameters
        ----------
        ticker : str
            Stock ticker symbol.
        trend : dict
            Output from TrendAnalysisAgent.run(). Must contain:
            trend, rsi, atr_pct, macd_histogram, volume_surge,
            swing_setup, sma_short, sma_long, annualised_volatility.

        Returns
        -------
        dict
            Keys:
            ticker, risk_score (1–10), risk_level, setup_quality,
            tradeable, atr_stop_pct, max_position_size_pct,
            half_position_target_atr, vol_signal, rsi_signal,
            momentum_signal, trend_signal, swing_setup, status.
        """
        trend_dir    = trend["trend"]
        rsi          = trend["rsi"]
        atr_pct      = trend["atr_pct"]
        macd_hist    = trend["macd_histogram"]
        volume_surge = trend["volume_surge"]
        swing_setup  = trend["swing_setup"]
        momentum_pct = trend["momentum_pct"]

        # ---- individual signals ----
        vol_signal      = self._score_volatility(atr_pct)
        rsi_signal      = self._score_rsi(rsi)
        momentum_signal = self._score_momentum(momentum_pct)
        trend_signal    = trend_dir

        # ---- raw risk score ----
        raw = (
            self._vol_points(vol_signal)
            + self._rsi_points(rsi_signal)
            + self._momentum_points(momentum_signal)
            + self._trend_points(trend_signal)
            + self._setup_points(swing_setup)
            + self._volume_points(volume_surge)
        )
        risk_score = self._normalise_score(raw)
        risk_level = self._classify_risk(risk_score)

        # ---- setup quality ----
        setup_quality = self._rate_setup(swing_setup, vol_signal, rsi_signal, volume_surge)

        # ---- position sizing (2% rule) ----
        # Stop is placed 1.5× ATR below entry.  This is tight enough that
        # taking half off at 1× ATR profit leaves the trailing stop at
        # (approx) break-even on the remaining half.
        atr_stop_mult             = 1.5
        atr_stop_pct              = atr_pct * atr_stop_mult          # % of price
        max_risk_gbp              = self.capital * self.risk_pct
        # position_size = max_risk / stop_distance
        # expressed as % of capital:
        max_position_size_pct     = (max_risk_gbp / (self.capital * atr_stop_pct / 100)) * 100
        max_position_size_pct     = min(max_position_size_pct, 20.0)  # hard cap at 20% per position

        # ---- tradeable flag ----
        # Only flag as tradeable if:
        # - There is a real setup (not 'none')
        # - Not a downtrend or sideways market
        # - RSI is not at an extreme
        # - Volatility is manageable
        tradeable = (
            swing_setup != "none"
            and trend_dir == "uptrend"
            and rsi_signal == "neutral"
            and vol_signal != "dangerous"
        )

        return {
            "ticker"                   : ticker,
            "risk_score"               : risk_score,
            "risk_level"               : risk_level,
            "setup_quality"            : setup_quality,
            "tradeable"                : tradeable,
            # Position management
            "atr_stop_pct"             : round(atr_stop_pct, 4),
            "max_position_size_pct"    : round(max_position_size_pct, 2),
            "half_position_target_atr" : round(atr_pct * 1.0, 4),  # 1× ATR = half-off target
            # Signals (for downstream / display)
            "vol_signal"               : vol_signal,
            "rsi_signal"               : rsi_signal,
            "momentum_signal"          : momentum_signal,
            "trend_signal"             : trend_signal,
            "swing_setup"              : swing_setup,
            "status"                   : "ok",
        }

    # ------------------------------------------------------------------
    # Signal classifiers
    # ------------------------------------------------------------------

    def _score_volatility(self, atr_pct: float) -> str:
        """
        Classify ATR% into a swing-volatility signal.

        For swing trades, we *want* some volatility (price needs to move),
        but not so much that a 2% capital stop is triggered by intraday noise.
        """
        if atr_pct < self.vol_low:
            return "low"        # stock barely moves — hard to hit 10%
        elif atr_pct <= self.vol_high:
            return "medium"     # ideal swing range
        return "dangerous"      # too wide to manage with 2% rule

    def _score_rsi(self, rsi: float) -> str:
        if rsi < self.rsi_oversold:
            return "oversold"       # could be a falling knife
        elif rsi > self.rsi_overbought:
            return "overbought"     # overextended, reversal risk
        return "neutral"

    def _score_momentum(self, momentum_pct: float) -> str:
        if momentum_pct > 10.0:
            return "strong"
        elif momentum_pct > 0:
            return "positive"
        elif momentum_pct > -10.0:
            return "weak"
        return "negative"

    # ------------------------------------------------------------------
    # Point mappings  (lower raw score = lower risk = better)
    # ------------------------------------------------------------------

    def _vol_points(self, signal: str) -> int:
        return {"low": 2, "medium": 1, "dangerous": 4}[signal]

    def _rsi_points(self, signal: str) -> int:
        return {"oversold": 3, "neutral": 1, "overbought": 3}[signal]

    def _momentum_points(self, signal: str) -> int:
        return {"strong": 1, "positive": 2, "weak": 3, "negative": 4}[signal]

    def _trend_points(self, signal: str) -> int:
        return {"uptrend": 1, "sideways": 3, "downtrend": 5}[signal]

    def _setup_points(self, setup: str) -> int:
        return {"breakout": 1, "pullback": 1, "momentum": 2, "none": 4}[setup]

    def _volume_points(self, surge: bool) -> int:
        return 1 if surge else 2

    # ------------------------------------------------------------------
    # Score normalisation & classification
    # ------------------------------------------------------------------

    def _normalise_score(self, raw: int) -> int:
        """
        Normalise raw point total to a 1–10 risk score.

        Raw range: 6 (perfect setup) to 22 (everything wrong).
        Mapped linearly to 1–10.
        """
        raw_min, raw_max = 6, 22
        normalised = (raw - raw_min) / (raw_max - raw_min)
        return round(1 + normalised * 9)

    def _classify_risk(self, score: int) -> str:
        if score <= 3:
            return "low"
        elif score <= 6:
            return "medium"
        return "high"

    def _rate_setup(
        self,
        swing_setup: str,
        vol_signal: str,
        rsi_signal: str,
        volume_surge: bool,
    ) -> str:
        """
        Rates the overall swing setup quality as 'A', 'B', 'C', or 'none'.

        A — textbook setup: right type, ideal vol, neutral RSI, volume confirms
        B — good setup with one imperfect factor
        C — marginal setup
        none — no valid setup
        """
        if swing_setup == "none":
            return "none"

        score = 0
        if swing_setup in ("breakout", "pullback"):
            score += 2
        else:
            score += 1  # momentum

        if vol_signal == "medium":
            score += 2
        elif vol_signal == "low":
            score += 1

        if rsi_signal == "neutral":
            score += 2

        if volume_surge:
            score += 1

        if score >= 7:
            return "A"
        elif score >= 5:
            return "B"
        return "C"
