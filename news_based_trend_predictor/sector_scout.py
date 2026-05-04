"""
sector_scout.py — Orchestrates the full pipeline:

    scrape headlines -> LLM analysis -> emit SectorSignals

Usage
-----
Run directly (once, then exits):

    python sector_scout.py

Cron example (every Sunday at 07:00):

    0 7 * * 0 /path/to/venv/bin/python /path/to/sector_scout.py >> /var/log/sector_scout.log 2>&1

Embedded in your robotrader:

    from sector_scout import SectorScout, SectorSignal

    scout   = SectorScout()
    signals = scout.run_cycle()

    sector_weights = {
        sig.sector: sig.confidence
        for sig in signals
        if sig.confidence >= 0.65
           and sig.disruption_strength >= 0.55
    }

SectorSignal fields
-------------------
    sector               : str    - ICB sector name
    confidence           : float  - 0-1, LLM conviction in positive movement
    disruption_type      : str    - taxonomy category
    disruption_strength  : float  - 0-1, magnitude of the structural break
    time_to_impact_weeks : int    - estimated weeks before full market pricing
    propagation          : str    - mechanism sentence
    invalidation_risk    : str    - what kills the thesis (with probability)
    rationale            : str    - overall case summary
    bear_case_probability: float  - probability of negative/flat outcome
    key_catalysts        : list   - specific upcoming confirming events
    correlated_sectors   : list   - secondary sector plays
    conviction_drivers   : list   - specific headlines supporting the signal
    macro_regime_summary : str    - macro backdrop at time of analysis
    headlines            : list   - Headline objects that drove the signal
    timestamp            : datetime
    cycle_id             : int
"""

from __future__ import annotations

import logging
import sys
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional

from config import LSE_SECTORS, SCHEDULER_CFG
from llm_analyzer import analyse_headlines
from news_fetcher import Headline, collect_headlines

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s - %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sector_scout")


# ---------------------------------------------------------------------------
# Output data model
# ---------------------------------------------------------------------------

@dataclass
class SectorSignal:
    sector:               str
    confidence:           float
    disruption_type:      str
    disruption_strength:  float
    time_to_impact_weeks: int
    propagation:          str
    invalidation_risk:    str
    rationale:            str
    bear_case_probability: float
    key_catalysts:        List[str]
    correlated_sectors:   List[str]
    conviction_drivers:   List[str]
    macro_regime_summary: str
    headlines:            List[Headline]
    timestamp:            datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cycle_id:             int      = 0

    def __repr__(self) -> str:
        return (
            f"SectorSignal(sector={self.sector!r}, "
            f"conf={self.confidence:.0%}, "
            f"bear={self.bear_case_probability:.0%}, "
            f"disruption={self.disruption_type!r}, "
            f"impact={self.time_to_impact_weeks}w)"
        )

    def to_dict(self) -> dict:
        return {
            "sector":               self.sector,
            "confidence":           self.confidence,
            "disruption_type":      self.disruption_type,
            "disruption_strength":  self.disruption_strength,
            "time_to_impact_weeks": self.time_to_impact_weeks,
            "propagation":          self.propagation,
            "invalidation_risk":    self.invalidation_risk,
            "rationale":            self.rationale,
            "bear_case_probability": self.bear_case_probability,
            "key_catalysts":        self.key_catalysts,
            "correlated_sectors":   self.correlated_sectors,
            "conviction_drivers":   self.conviction_drivers,
            "macro_regime_summary": self.macro_regime_summary,
            "timestamp":            self.timestamp.isoformat(),
            "cycle_id":             self.cycle_id,
            "headline_count":       len(self.headlines),
        }


# ---------------------------------------------------------------------------
# Scout
# ---------------------------------------------------------------------------

class SectorScout:
    """
    Scrapes financial news and uses an LLM via OpenRouter to identify
    medium-term (2-6 week) swing opportunities in LSE sectors driven by
    structural disruptions.
    """

    def __init__(
        self,
        on_signal:      Optional[Callable[[List[SectorSignal]], None]] = None,
        min_confidence: Optional[float] = None,
        min_disruption: Optional[float] = None,
    ):
        self._on_signal      = on_signal or self._default_logger
        self._min_confidence = min_confidence or SCHEDULER_CFG.min_confidence
        self._min_disruption = min_disruption or SCHEDULER_CFG.min_disruption_strength
        self._cycle_id       = 0

    # ------------------------------------------------------------------
    # Core cycle
    # ------------------------------------------------------------------

    def run_cycle(self) -> List[SectorSignal]:
        self._cycle_id += 1
        cycle = self._cycle_id
        logger.info("=== Cycle %d started ===", cycle)

        headlines = collect_headlines(max_total=SCHEDULER_CFG.max_headlines_per_cycle)
        if not headlines:
            logger.warning("Cycle %d: no headlines - skipping LLM.", cycle)
            return []

        raw_signals = analyse_headlines(headlines)

        signals: List[SectorSignal] = []
        for raw in raw_signals:
            if raw["confidence"] < self._min_confidence:
                continue
            if raw["disruption_strength"] < self._min_disruption:
                continue

            kws = [kw.lower() for kw in LSE_SECTORS.get(raw["sector"], [])]
            relevant = [
                h for h in headlines
                if any(kw in h.title.lower() or kw in h.summary.lower() for kw in kws)
            ] or headlines[:5]

            signals.append(
                SectorSignal(
                    sector               = raw["sector"],
                    confidence           = raw["confidence"],
                    disruption_type      = raw["disruption_type"],
                    disruption_strength  = raw["disruption_strength"],
                    time_to_impact_weeks = raw["time_to_impact_weeks"],
                    propagation          = raw["propagation"],
                    invalidation_risk    = raw["invalidation_risk"],
                    rationale            = raw["rationale"],
                    bear_case_probability = raw.get("bear_case_probability", 1.0 - raw["confidence"]),
                    key_catalysts        = raw.get("key_catalysts", []),
                    correlated_sectors   = raw.get("correlated_sectors", []),
                    conviction_drivers   = raw.get("conviction_drivers", []),
                    macro_regime_summary = raw.get("macro_regime_summary", ""),
                    headlines            = relevant,
                    cycle_id             = cycle,
                )
            )

        logger.info(
            "Cycle %d - %d swing signals above thresholds (conf>=%.0f%%, disr>=%.0f%%).",
            cycle, len(signals),
            self._min_confidence * 100,
            self._min_disruption * 100,
        )

        if signals:
            self._on_signal(signals)

        return signals

    # ------------------------------------------------------------------
    # Default console renderer
    # ------------------------------------------------------------------

    @staticmethod
    def _default_logger(signals: List[SectorSignal]) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Print macro regime summary once at the top (from first signal)
        macro = signals[0].macro_regime_summary if signals else ""

        print("\n" + "=" * 72)
        print(f"  LSE SWING SIGNALS  .  {ts}")
        print("=" * 72)

        if macro:
            print(f"\n  MACRO REGIME")
            print(f"  {macro}")

        for sig in signals:
            bull_bar = "#" * int(sig.confidence * 20)
            bear_bar = "#" * int(sig.bear_case_probability * 20)
            print(f"\n  {'=' * 68}")
            print(f"  {sig.sector:<26}  BULL {sig.confidence:.0%}  [{bull_bar:<20}]")
            print(f"  {'':26}  BEAR {sig.bear_case_probability:.0%}  [{bear_bar:<20}]")
            print(f"  Disruption  : {sig.disruption_type}  (strength {sig.disruption_strength:.0%})")
            print(f"  Impact      : ~{sig.time_to_impact_weeks} week(s)")
            print(f"  Mechanism   : {sig.propagation}")
            print(f"  Rationale   : {sig.rationale}")
            print(f"  Kill switch : {sig.invalidation_risk}")
            if sig.key_catalysts:
                print(f"  Catalysts   : {' | '.join(sig.key_catalysts)}")
            if sig.correlated_sectors:
                print(f"  Also watch  : {', '.join(sig.correlated_sectors)}")
            if sig.conviction_drivers:
                print(f"  Evidence    :")
                for d in sig.conviction_drivers:
                    print(f"    - {d}")

        print("\n" + "=" * 72 + "\n")


# ---------------------------------------------------------------------------
# CLI - run once and exit
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    os.environ["OPENROUTER_API_KEY"] = ''#Your API KEY here
    scout   = SectorScout()
    signals = scout.run_cycle()
    sys.exit(0 if signals is not None else 1)
