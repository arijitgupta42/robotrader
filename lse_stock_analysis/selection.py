"""
Rule-based stock selection and the weekly Universe Snapshot.

Given the scout's Sector Signals and the cleaned Universe prices:

1. every Universe stock is analysed with the existing trend and risk agents
   (parameters as in main.py, unchanged);
2. the Candidates for each Signalled Sector are the stocks whose Primary Sector
   it is (Secondary Sectors are recorded but never used for selection);
3. a Candidate is eligible when it is tradeable, has Setup Grade A or B, has no
   Price Anomaly and has fresh prices;
4. eligible Candidates are ranked by Setup Grade, then risk score (lower first),
   then ticker, and the top few become the Picks — how many depends on the
   signal's confidence (see picks_allowed()).

No LLM is involved, so a past week can be replayed exactly.  Position size is
whatever the risk agent's 2% rule gives.
"""
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from .agents.risk_scoring_agent import RiskScoringAgent
from .agents.trend_analysis_agent import TrendAnalysisAgent
from .prices import PriceFetchResult
from .universe import load_sector_map

logger = logging.getLogger(__name__)

# Bump whenever a number or rule in this module changes (TREND_PARAMS, RISK_PARAMS, the pick-count
# thresholds, the eligibility rules, the ranking) or in the agents it calls.  It is stored on every
# Universe Snapshot row, so the methodology review can compare the weeks before and after a change.
METHODOLOGY_VERSION = "2026-10-03.1"

# Same parameters as lse_stock_analysis/main.py — keep the two in sync.
TREND_PARAMS = dict(
    sma_short=20, sma_long=50, rsi_period=14, atr_period=14, bb_period=20,
    macd_fast=12, macd_slow=26, macd_signal=9, sideways_threshold=0.02,
    volume_surge_mult=1.5, pullback_rsi_max=45.0, breakout_lookback=20,
)
RISK_PARAMS = dict(
    capital=1000.0, risk_pct=0.02, vol_low=1.5, vol_high=4.0,
    rsi_oversold=30.0, rsi_overbought=70.0,
)

GRADE_RANK = {"A": 0, "B": 1}                      # only A and B are eligible
MID_CONFIDENCE = 0.65                              # below: 1 pick; from here to HIGH_CONFIDENCE: 2
HIGH_CONFIDENCE = 0.80                             # above: 3 picks
CAPPED_CONVERGENCE = ("Divergent", "Reddit-Led")   # at most one Pick
MAX_RUNNERS_UP = 3

TREND_METRICS = [
    "trend", "momentum_pct", "annualised_volatility", "rsi", "sma_short", "sma_long",
    "macd_line", "macd_signal_line", "macd_histogram", "atr", "atr_pct",
    "bb_upper", "bb_lower", "bb_width", "price_vs_bb", "volume_surge", "volume_ratio", "swing_setup",
]
RISK_METRICS = [
    "risk_score", "risk_level", "setup_quality", "tradeable", "atr_stop_pct",
    "max_position_size_pct", "half_position_target_atr", "vol_signal", "rsi_signal", "momentum_signal",
]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyse_universe(data: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """
    Run the trend and risk agents over every ticker's cleaned prices.

    Returns
    -------
    dict[str, dict]
        ticker → {"trend": trend dict, "risk": risk dict}.  A ticker whose
        analysis failed has "risk": None.
    """
    trend_agent = TrendAnalysisAgent(**TREND_PARAMS)
    risk_agent = RiskScoringAgent(**RISK_PARAMS)
    out: dict[str, dict] = {}
    for ticker, df in data.items():
        trend = trend_agent.run(ticker, df)
        risk = risk_agent.run(ticker, trend) if trend.get("status") == "ok" else None
        out[ticker] = {"trend": trend, "risk": risk if risk and risk.get("status") == "ok" else None}
    return out


# ---------------------------------------------------------------------------
# Selection rules
# ---------------------------------------------------------------------------

def picks_allowed(confidence: float, convergence_type: str) -> int:
    """
    How many Picks a Sector Signal earns: 1 below 0.65 confidence, 2 from 0.65
    to 0.80 inclusive, 3 above 0.80; Divergent and Reddit-Led signals get at most 1.
    """
    if confidence > HIGH_CONFIDENCE:
        n = 3
    elif confidence >= MID_CONFIDENCE:
        n = 2
    else:
        n = 1
    return min(n, 1) if convergence_type in CAPPED_CONVERGENCE else n


def _ineligible_reason(ticker: str, analysis: Optional[dict], prices: PriceFetchResult) -> Optional[str]:
    """Why a Candidate cannot be a Pick, or None if it is eligible."""
    if ticker not in prices.data or analysis is None or analysis["risk"] is None:
        if ticker in prices.short_history:
            return f"not enough price history ({prices.short_history[ticker]} bars)"
        return "no usable price data"
    if ticker in prices.price_anomalies:
        return f"Price Anomaly: moved {prices.price_anomalies[ticker]:.0%} in one day"
    if ticker in prices.stale:
        return f"stale prices (last bar {prices.stale[ticker]})"
    risk, trend = analysis["risk"], analysis["trend"]
    if not risk["tradeable"]:
        if risk["swing_setup"] == "none":
            return "no swing setup"
        if risk["trend_signal"] != "uptrend":
            return f"trend is {risk['trend_signal']}"
        if risk["rsi_signal"] != "neutral":
            return f"RSI {trend['rsi']:.0f} is {risk['rsi_signal']}"
        return f"volatility too high (ATR {trend['atr_pct']:.1f}%)"
    if risk["setup_quality"] not in GRADE_RANK:
        return f"setup grade {risk['setup_quality']} (below B)"
    return None


def _row(ticker: str, entry: dict, analysis: Optional[dict], prices: PriceFetchResult) -> dict:
    """Reporting dict for one stock (used for Picks and runners-up)."""
    row = {"ticker": ticker, "company": entry["company"]}
    if analysis and analysis["risk"]:
        t, r = analysis["trend"], analysis["risk"]
        row.update(                                   # plain Python numbers, so the result is JSON-serialisable
            close=round(float(prices.data[ticker]["Close"].iloc[-1]), 2) if ticker in prices.data else None,
            swing_setup=r["swing_setup"], setup_grade=r["setup_quality"], risk_score=int(r["risk_score"]),
            rsi=round(float(t["rsi"]), 1), atr_pct=round(float(t["atr_pct"]), 2),
            stop_loss_pct=round(float(r["atr_stop_pct"]), 2), max_position_pct=round(float(r["max_position_size_pct"]), 1),
        )
    return row


def _rank_key(row: dict):
    return (GRADE_RANK.get(row.get("setup_grade"), 9), row.get("risk_score", 99), row["ticker"])


@dataclass
class SectorSelection:
    """The outcome for one Signalled Sector."""

    sector: str
    confidence: float
    convergence_type: str
    picks_allowed: int
    investable: bool                                   # False → an Uninvestable Sector
    candidate_count: int
    picks: list[dict] = field(default_factory=list)
    runners_up: list[dict] = field(default_factory=list)   # each carries a "reason"

    def to_dict(self) -> dict:
        return asdict(self)


def select_stocks(
        signals:      list[dict],
        analysis:     dict[str, dict],
        prices:       PriceFetchResult,
        sector_map:   Optional[dict[str, dict]] = None,
        ) -> list[SectorSelection]:
    """
    Choose Picks for every Signalled Sector.

    Parameters
    ----------
    signals : list[dict]
        The scout's consolidated signals (needs sector, confidence, convergence_type).
        If a sector appears twice the higher-confidence signal is used.
    analysis : dict
        Output of analyse_universe().
    prices : PriceFetchResult
        From prices.fetch_universe_prices().
    sector_map : dict, optional
        Defaults to the committed Sector Map.

    Returns
    -------
    list[SectorSelection]
        In the order of `signals` (first occurrence of each sector).
    """
    sector_map = sector_map if sector_map is not None else load_sector_map()
    best: dict[str, dict] = {}
    order: list[str] = []
    for s in signals:
        if s["sector"] not in best:
            order.append(s["sector"])
        if s["sector"] not in best or s["confidence"] > best[s["sector"]]["confidence"]:
            best[s["sector"]] = s

    selections = []
    for sector in order:
        sig = best[sector]
        candidates = {t: e for t, e in sector_map.items() if e["primary_sector"] == sector}
        sel = SectorSelection(
            sector=sector, confidence=sig["confidence"], convergence_type=sig["convergence_type"],
            picks_allowed=picks_allowed(sig["confidence"], sig["convergence_type"]),
            investable=bool(candidates), candidate_count=len(candidates),
        )
        eligible, rejected = [], []
        for ticker, entry in candidates.items():
            reason = _ineligible_reason(ticker, analysis.get(ticker), prices)
            row = _row(ticker, entry, analysis.get(ticker), prices)
            (rejected if reason else eligible).append({**row, "reason": reason} if reason else row)
        eligible.sort(key=_rank_key)
        sel.picks = eligible[: sel.picks_allowed]
        leftovers = [{**r, "reason": "ranked below the pick limit"} for r in eligible[sel.picks_allowed:]]
        # runners-up: eligible leftovers first, then the closest rejected (has a setup, better grade, lower risk)
        rejected.sort(key=lambda r: (r.get("swing_setup", "none") == "none", ) + _rank_key(r))
        sel.runners_up = (leftovers + rejected)[:MAX_RUNNERS_UP]
        selections.append(sel)
    return selections


# ---------------------------------------------------------------------------
# Universe Snapshot
# ---------------------------------------------------------------------------

def week_label(ts: datetime) -> str:
    """ISO week label, e.g. '2026-W40' (same format as the scout's S3 prefixes)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    iso = ts.astimezone(timezone.utc).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def build_snapshot(
        signal_ts:   datetime,
        signals:     list[dict],
        analysis:    dict[str, dict],
        prices:      PriceFetchResult,
        selections:  list[SectorSelection],
        sector_map:  Optional[dict[str, dict]] = None,
        backfilled:  bool = False,
        prompt_version: str = "unversioned",
        ) -> pd.DataFrame:
    """
    One row per Universe stock for the weekly snapshot (snapshots/YYYY-Www/universe.csv).

    Every stock is included, with or without data, so later weeks can compare
    Candidates and Picks with the whole Universe.  Forward Returns are read
    from the `close` column of later snapshots.
    """
    sector_map = sector_map if sector_map is not None else load_sector_map()
    signal_by_sector: dict[str, dict] = {}
    for s in signals:
        if s["sector"] not in signal_by_sector or s["confidence"] > signal_by_sector[s["sector"]]["confidence"]:
            signal_by_sector[s["sector"]] = s
    picked = {p["ticker"] for sel in selections for p in sel.picks}

    rows = []
    for ticker in sorted(sector_map):
        entry = sector_map[ticker]
        a = analysis.get(ticker)
        in_prices = ticker in prices.data
        sig = signal_by_sector.get(entry["primary_sector"]) if entry["primary_sector"] else None
        if in_prices and a and a["risk"]:
            status = "ok"
        elif in_prices:
            status = "analysis_failed"
        elif ticker in prices.short_history:
            status = "short_history"
        else:
            status = "no_data"
        row = {
            "week": week_label(signal_ts),
            "signal_ts": signal_ts.isoformat(),
            "price_cutoff": prices.cutoff_date.isoformat(),
            "backfilled": backfilled,
            "methodology_version": METHODOLOGY_VERSION,
            "prompt_version": prompt_version,
            "ticker": ticker,
            "company": entry["company"],
            "index": entry["index"],
            "primary_sector": entry["primary_sector"],
            "secondary_sectors": "|".join(entry["secondary_sectors"]),
            "status": status,
            "last_bar": prices.last_bar[ticker].isoformat() if ticker in prices.last_bar else None,
            "close": round(float(prices.data[ticker]["Close"].iloc[-1]), 4) if in_prices else None,
            "price_anomaly": ticker in prices.price_anomalies,
            "max_daily_move": prices.price_anomalies.get(ticker),
            "unit_fixed": prices.unit_fixed.get(ticker, 0),
            "stale": ticker in prices.stale,
        }
        for k in TREND_METRICS:
            row[k] = a["trend"].get(k) if status == "ok" else None
        for k in RISK_METRICS:
            row["setup_grade" if k == "setup_quality" else k] = a["risk"].get(k) if status == "ok" else None
        row.update(
            is_candidate=sig is not None,
            is_pick=ticker in picked,
            signal_confidence=sig["confidence"] if sig else None,
            signal_convergence_type=sig["convergence_type"] if sig else None,
        )
        rows.append(row)
    return pd.DataFrame(rows)


def snapshot_to_csv(snapshot: pd.DataFrame) -> str:
    """CSV text for S3 (one row per stock, no index)."""
    return snapshot.to_csv(index=False)
