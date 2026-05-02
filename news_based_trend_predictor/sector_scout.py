"""
sector_scout.py — Orchestrates the full pipeline:

    scrape headlines → Gemma 4 E2B analysis → emit SectorSignals

Usage
-----
Standalone (prints signals to stdout):

    python sector_scout.py

Embedded in your robotrader (recommended):

    from sector_scout import SectorScout, SectorSignal, SignalBuffer

    buffer = SignalBuffer(maxlen=20)
    scout  = SectorScout(on_signal=buffer.ingest)
    scout.start()

    # In your trading loop — poll the buffer at your own cadence:
    sector_weights = {
        sig.sector: sig.confidence
        for sig in buffer.latest_by_sector().values()
        if sig.confidence >= 0.65
           and sig.disruption_strength >= 0.55
    }

SectorSignal fields
-------------------
    sector               : str    — ICB sector name
    confidence           : float  — 0–1, LLM conviction in positive movement
    disruption_type      : str    — taxonomy category (e.g. "Supply Chain Dislocation")
    disruption_strength  : float  — 0–1, magnitude of the structural break
    time_to_impact_weeks : int    — estimated weeks before full market pricing
    propagation          : str    — mechanism sentence
    invalidation_risk    : str    — what kills the thesis
    rationale            : str    — overall case summary
    headlines            : list   — Headline objects that drove the signal
    timestamp            : datetime
    cycle_id             : int
"""

from __future__ import annotations

import logging
import signal
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import SCHEDULER_CFG
from llm_analyzer import analyse_headlines
from news_fetcher import Headline, collect_headlines

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
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
    headlines:            List[Headline]
    timestamp:            datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cycle_id:             int      = 0

    def __repr__(self) -> str:
        return (
            f"SectorSignal(sector={self.sector!r}, "
            f"conf={self.confidence:.0%}, "
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
            "timestamp":            self.timestamp.isoformat(),
            "cycle_id":             self.cycle_id,
            "headline_count":       len(self.headlines),
        }


# ---------------------------------------------------------------------------
# Scout
# ---------------------------------------------------------------------------

class SectorScout:
    """
    Periodically scrapes financial news and uses Gemma 4 E2B-it to identify
    medium-term (2–6 week) swing opportunities in LSE sectors driven by
    structural disruptions.
    """

    def __init__(
        self,
        on_signal:        Optional[Callable[[List[SectorSignal]], None]] = None,
        interval_minutes: Optional[int]   = None,
        min_confidence:   Optional[float] = None,
        min_disruption:   Optional[float] = None,
    ):
        self._on_signal         = on_signal or self._default_logger
        self._interval_min      = interval_minutes or SCHEDULER_CFG.interval_minutes
        self._min_confidence    = min_confidence   or SCHEDULER_CFG.min_confidence
        self._min_disruption    = min_disruption   or SCHEDULER_CFG.min_disruption_strength
        self._cycle_id          = 0
        self._scheduler: Optional[BackgroundScheduler] = None

    # ------------------------------------------------------------------
    # Core cycle
    # ------------------------------------------------------------------

    def run_cycle(self) -> List[SectorSignal]:
        self._cycle_id += 1
        cycle = self._cycle_id
        logger.info("=== Cycle %d started ===", cycle)

        headlines = collect_headlines(max_total=SCHEDULER_CFG.max_headlines_per_cycle)
        if not headlines:
            logger.warning("Cycle %d: no headlines — skipping LLM.", cycle)
            return []

        raw_signals = analyse_headlines(headlines)

        signals: List[SectorSignal] = []
        for raw in raw_signals:
            if raw["confidence"] < self._min_confidence:
                continue
            if raw["disruption_strength"] < self._min_disruption:
                continue

            # Attach relevant headlines for this sector
            from config import LSE_SECTORS
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
                    headlines            = relevant,
                    cycle_id             = cycle,
                )
            )

        logger.info(
            "Cycle %d — %d swing signals above thresholds (conf≥%.0f%%, disr≥%.0f%%).",
            cycle, len(signals),
            self._min_confidence * 100,
            self._min_disruption * 100,
        )
        if signals:
            self._on_signal(signals)
        return signals

    # ------------------------------------------------------------------
    # Scheduler control
    # ------------------------------------------------------------------

    # def start(self, run_immediately: bool = True) -> None:
    #     if self._scheduler and self._scheduler.running:
    #         logger.warning("SectorScout already running.")
    #         return
    #     if run_immediately:
    #         try:
    #             self.run_cycle()
    #         except Exception as exc:
    #             logger.error("Initial cycle error: %s", exc)

    #     self._scheduler = BackgroundScheduler(timezone="UTC")
    #     self._scheduler.add_job(
    #         func    = self._safe_cycle,
    #         trigger = IntervalTrigger(minutes=self._interval_min),
    #         id      = "sector_scout_cycle",
    #         name    = f"Sector Scout (every {self._interval_min} min)",
    #         replace_existing = True,
    #     )
    #     self._scheduler.start()
    #     logger.info("SectorScout running — next cycle in %d min.", self._interval_min)

    # def stop(self) -> None:
    #     if self._scheduler and self._scheduler.running:
    #         self._scheduler.shutdown(wait=False)
    #         logger.info("SectorScout stopped.")

    # def _safe_cycle(self) -> None:
    #     try:
    #         self.run_cycle()
    #     except Exception as exc:
    #         logger.error("Unhandled exception in cycle %d: %s", self._cycle_id, exc, exc_info=True)

    # ------------------------------------------------------------------
    # Default console renderer
    # ------------------------------------------------------------------

    @staticmethod
    def _default_logger(signals: List[SectorSignal]) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        print("\n" + "═" * 72)
        print(f"  LSE SWING SIGNALS  ·  {ts}")
        print("═" * 72)
        for sig in signals:
            bar = "█" * int(sig.confidence * 20) + "░" * (20 - int(sig.confidence * 20))
            print(f"\n  {sig.sector:<26} {sig.confidence:.0%}  {bar}")
            print(f"  Disruption : {sig.disruption_type}  (strength {sig.disruption_strength:.0%})")
            print(f"  Impact     : ~{sig.time_to_impact_weeks} week(s)")
            print(f"  Mechanism  : {sig.propagation}")
            print(f"  Risk       : {sig.invalidation_risk}")
        print("\n" + "═" * 72 + "\n")


# ---------------------------------------------------------------------------
# Thread-safe signal buffer for poll-based integration
# ---------------------------------------------------------------------------

class SignalBuffer:
    """
    Ring buffer of SectorSignal objects, safe for cross-thread access.

    Your deterministic trader polls this rather than using callbacks.

    Example
    -------
        buffer = SignalBuffer(maxlen=50)
        scout  = SectorScout(on_signal=buffer.ingest)
        scout.start()

        # Trading loop:
        for sector, sig in buffer.latest_by_sector().items():
            if sig.confidence >= 0.70 and sig.time_to_impact_weeks <= 3:
                trader.increase_sector_exposure(sector, sig.confidence)
    """

    def __init__(self, maxlen: int = 50):
        from collections import deque
        import threading
        self._buf  = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def ingest(self, signals: List[SectorSignal]) -> None:
        with self._lock:
            for sig in signals:
                self._buf.append(sig)

    def latest(self) -> List[SectorSignal]:
        with self._lock:
            return list(self._buf)

    def latest_by_sector(self) -> dict[str, SectorSignal]:
        """Most recent signal per sector (later entries overwrite)."""
        result: dict[str, SectorSignal] = {}
        with self._lock:
            for sig in self._buf:
                result[sig.sector] = sig
        return result

    def high_conviction(
        self,
        min_confidence: float = 0.70,
        min_disruption: float = 0.60,
        max_weeks:      int   = 4,
    ) -> List[SectorSignal]:
        """
        Convenience filter: return signals that meet all three thresholds.
        This is the subset your trader should act on most aggressively.
        """
        return [
            sig for sig in self.latest_by_sector().values()
            if (sig.confidence         >= min_confidence
                and sig.disruption_strength >= min_disruption
                and sig.time_to_impact_weeks <= max_weeks)
        ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    scout = SectorScout()
    scout.run_cycle()

    # def _shutdown(signum, frame):
    #     logger.info("Shutdown signal received.")
    #     scout.stop()
    #     sys.exit(0)

    # signal.signal(signal.SIGINT,  _shutdown)
    # signal.signal(signal.SIGTERM, _shutdown)

    # logger.info("SectorScout starting in foreground. Ctrl-C to stop.")
    # scout.start(run_immediately=True)

    # import time
    # while True:
    #     time.sleep(60)
