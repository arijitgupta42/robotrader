"""
sector_scout.py — Orchestrates the full pipeline:

    scrape headlines  ─┐
                        ├─► LLM pass 1 (news)   ─┐
    scrape Reddit     ─┘                           ├─► LLM pass 3 (consolidate) ─► emit ConsolidatedSignals
                        └─► LLM pass 2 (reddit) ─┘

Usage
-----
Run directly (once, then exits):

    python sector_scout.py

Cron example (every Saturday at 07:00):

    0 7 * * 0 /path/to/venv/bin/python /path/to/sector_scout.py >> /var/log/sector_scout.log 2>&1

Embedded in your robotrader:

    from sector_scout import SectorScout, ConsolidatedSignal

    scout   = SectorScout()
    signals, llm_succeeded = scout.run_cycle()

    sector_weights = {
        sig.sector: sig.confidence
        for sig in signals
        if sig.confidence >= 0.65
           and sig.disruption_strength >= 0.55
           and sig.convergence_type in ("Convergent", "News-Led")
    }

ConsolidatedSignal fields
--------------------------
    sector               : str    - LSE sub-sector name
    confidence           : float  - 0-1, consolidated LLM conviction
    disruption_type      : str    - taxonomy category
    disruption_strength  : float  - 0-1, magnitude of structural break
    time_to_impact_weeks : int    - estimated weeks before full market pricing
    bear_case_probability: float  - probability of negative/flat outcome
    convergence_type     : str    - Convergent | News-Led | Reddit-Led | Divergent
    convergence_note     : str    - why news+reddit agree or disagree
    propagation          : str    - mechanism sentence
    invalidation_risk    : str    - what kills the thesis (with probability)
    rationale            : str    - overall consolidated case
    retail_thesis        : str    - what the Reddit crowd believes
    key_catalysts        : list   - specific upcoming confirming events
    correlated_sectors   : list   - secondary sector plays
    conviction_drivers   : list   - evidence items tagged [Source: News|Reddit]
    macro_regime_summary : str    - macro backdrop at time of analysis
    timestamp            : datetime

Pipeline failure modes
-----------------------
    llm_succeeded=False  — the consolidation LLM call failed.  Signals (if any)
                           are either raw news signals wrapped as ConsolidatedSignal
                           objects (convergence_type="News-Led") or empty.
                           Lambda handler treats this as a failure and retries.

    reddit_succeeded=False — Reddit LLM pass failed or Reddit was unreachable.
                             Pipeline continues with news signals only; the
                             consolidator still runs but sees no Reddit input.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional

from config import LSE_SECTORS, SCHEDULER_CFG, REDDIT_CFG
from llm_analyzer import analyse_headlines
from news_fetcher import Headline, collect_headlines
from reddit_fetcher import RedditPost, collect_reddit_posts
from reddit_analyzer import RedditSentimentSignal, analyse_reddit_posts
from consolidator import ConsolidatedSignal, consolidate_signals

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s - %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sector_scout")


# ---------------------------------------------------------------------------
# Legacy SectorSignal — kept for backwards compatibility with any callers
# that import it directly.  New code should use ConsolidatedSignal.
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
    Orchestrates the three-pass pipeline:

      Pass 1 — News LLM (llm_analyzer.py)
        Reads RSS/scrape headlines → raw SectorSignal dicts

      Pass 2 — Reddit LLM (reddit_analyzer.py)
        Reads Reddit posts → RedditSentimentSignal objects
        Runs concurrently with Pass 1 data collection; analysis is sequential.

      Pass 3 — Consolidator LLM (consolidator.py)
        Merges Pass 1 + Pass 2 → ConsolidatedSignal objects
        Labels each signal as Convergent / News-Led / Reddit-Led / Divergent.

    run_cycle() returns (List[ConsolidatedSignal], llm_succeeded).
    """

    def __init__(
        self,
        on_signal:           Optional[Callable[[List[ConsolidatedSignal]], None]] = None,
        min_confidence:      Optional[float] = None,
        min_disruption:      Optional[float] = None,
        skip_reddit:         bool = False,
    ):
        self._on_signal      = on_signal or self._default_logger
        self._min_confidence = min_confidence or SCHEDULER_CFG.min_confidence
        self._min_disruption = min_disruption or SCHEDULER_CFG.min_disruption_strength
        self._skip_reddit    = skip_reddit
        self._cycle_id       = 0

    # ------------------------------------------------------------------
    # Core cycle
    # ------------------------------------------------------------------

    def run_cycle(self) -> tuple[List[ConsolidatedSignal], bool]:
        """
        Returns (consolidated_signals, llm_succeeded).

        llm_succeeded reflects whether the consolidation LLM call succeeded.
        If False, signals are wrapped raw news signals (or empty) and the
        Lambda handler will leave the retry window open.

        Individual pass outcomes are logged but do not abort the pipeline:
          - If the news LLM fails → keyword fallback is used (low quality,
            but the consolidator will still run and label them accordingly).
          - If the Reddit LLM fails → consolidator runs with news-only input.
          - If the consolidator fails → news signals are wrapped and returned
            with llm_succeeded=False so the Lambda retries.
        """
        self._cycle_id += 1
        cycle = self._cycle_id
        logger.info("=== Cycle %d started ===", cycle)

        # ------------------------------------------------------------------
        # Step 1: Collect news headlines
        # ------------------------------------------------------------------
        logger.info("--- Step 1/4: Collecting news headlines ---")
        headlines = collect_headlines(max_total=SCHEDULER_CFG.max_headlines_per_cycle)
        if not headlines:
            logger.warning("Cycle %d: no headlines — skipping all LLM passes.", cycle)
            return [], False

        # ------------------------------------------------------------------
        # Step 2: Collect Reddit posts
        # ------------------------------------------------------------------
        reddit_posts: List[RedditPost] = []
        if not self._skip_reddit:
            logger.info("--- Step 2/4: Collecting Reddit posts ---")
            try:
                reddit_posts = collect_reddit_posts(max_total=REDDIT_CFG.max_posts_per_cycle)
            except Exception as exc:
                logger.warning(
                    "Cycle %d: Reddit collection failed (%s) — continuing without Reddit.", cycle, exc
                )
        else:
            logger.info("--- Step 2/4: Reddit collection skipped (skip_reddit=True) ---")

        # ------------------------------------------------------------------
        # Step 3a: News LLM pass
        # ------------------------------------------------------------------
        logger.info("--- Step 3a/4: News LLM pass (%d headlines) ---", len(headlines))
        raw_news_signals, news_llm_ok = analyse_headlines(headlines)

        if not news_llm_ok:
            logger.warning(
                "Cycle %d: news LLM failed — signals are keyword-heuristic only.", cycle
            )

        # Filter by thresholds before passing to consolidator
        filtered_news: list = [
            s for s in raw_news_signals
            if s["confidence"] >= self._min_confidence
            and s["disruption_strength"] >= self._min_disruption
        ]
        logger.info(
            "Cycle %d: %d/%d news signals passed thresholds (conf>=%.0f%%, disr>=%.0f%%).",
            cycle, len(filtered_news), len(raw_news_signals),
            self._min_confidence * 100, self._min_disruption * 100,
        )

        # Attach headline objects to news signals (for audit / S3 storage)
        news_signal_objects: List[SectorSignal] = []
        for raw in filtered_news:
            kws = [kw.lower() for kw in LSE_SECTORS.get(raw["sector"], [])]
            relevant = [
                h for h in headlines
                if any(kw in h.title.lower() or kw in h.summary.lower() for kw in kws)
            ] or headlines[:5]
            news_signal_objects.append(SectorSignal(
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
            ))

        # Extract macro regime from the news pass for the consolidator prompt
        macro_regime = (
            news_signal_objects[0].macro_regime_summary
            if news_signal_objects
            else raw_news_signals[0].get("macro_regime_summary", "") if raw_news_signals
            else ""
        )

        # ------------------------------------------------------------------
        # Step 3b: Reddit LLM pass
        # ------------------------------------------------------------------
        reddit_signals: List[RedditSentimentSignal] = []
        reddit_llm_ok = False

        if reddit_posts:
            logger.info("--- Step 3b/4: Reddit LLM pass (%d posts) ---", len(reddit_posts))
            try:
                reddit_signals, reddit_llm_ok = analyse_reddit_posts(reddit_posts)
                if not reddit_llm_ok:
                    logger.warning(
                        "Cycle %d: Reddit LLM failed — consolidator will run with news-only input.",
                        cycle,
                    )
                else:
                    # Filter weak Reddit signals before passing to consolidator
                    reddit_signals = [
                        s for s in reddit_signals
                        if s.bullish_conviction >= REDDIT_CFG.min_reddit_conviction
                    ]
                    logger.info(
                        "Cycle %d: %d Reddit signals passed conviction threshold (>=%.0f%%).",
                        cycle, len(reddit_signals), REDDIT_CFG.min_reddit_conviction * 100,
                    )
            except Exception as exc:
                logger.warning(
                    "Cycle %d: Reddit LLM pass raised an exception (%s) — continuing without Reddit.",
                    cycle, exc,
                )
        else:
            logger.info("--- Step 3b/4: Reddit LLM pass skipped (no posts collected) ---")

        # ------------------------------------------------------------------
        # Step 4: Consolidation LLM pass
        # ------------------------------------------------------------------
        logger.info(
            "--- Step 4/4: Consolidation pass (%d news signals, %d Reddit signals) ---",
            len(news_signal_objects), len(reddit_signals),
        )

        consolidated, consolidator_ok = consolidate_signals(
            news_signals    = news_signal_objects,
            reddit_signals  = reddit_signals,
            macro_regime    = macro_regime,
            min_confidence  = self._min_confidence,
        )

        # llm_succeeded = True only when the consolidation LLM itself succeeded.
        # Even if the news or Reddit passes were heuristic/empty, as long as the
        # consolidator ran properly we consider the cycle valid.
        llm_succeeded = consolidator_ok and news_llm_ok

        logger.info(
            "Cycle %d complete — %d consolidated signals. "
            "news_llm=%s  reddit_llm=%s  consolidator=%s  overall_succeeded=%s",
            cycle, len(consolidated),
            news_llm_ok, reddit_llm_ok, consolidator_ok, llm_succeeded,
        )

        if consolidated and llm_succeeded:
            self._on_signal(consolidated)

        return consolidated, llm_succeeded

    # ------------------------------------------------------------------
    # Convenience: expose raw intermediate results for callers that want them
    # ------------------------------------------------------------------

    def run_cycle_verbose(self) -> dict:
        """
        Like run_cycle() but returns a dict with all intermediate results:
            {
              "signals":          List[ConsolidatedSignal],
              "llm_succeeded":    bool,
              "news_signals":     List[SectorSignal],    # pre-consolidation
              "reddit_signals":   List[RedditSentimentSignal],
              "macro_regime":     str,
            }
        Useful for local development / debugging.
        """
        # Re-run the full pipeline step by step and capture intermediates.
        self._cycle_id += 1
        cycle = self._cycle_id

        headlines = collect_headlines(max_total=SCHEDULER_CFG.max_headlines_per_cycle)
        reddit_posts: List[RedditPost] = []
        if not self._skip_reddit:
            try:
                reddit_posts = collect_reddit_posts(max_total=REDDIT_CFG.max_posts_per_cycle)
            except Exception as exc:
                logger.warning("Verbose: Reddit collection failed: %s", exc)

        raw_news, news_ok = analyse_headlines(headlines) if headlines else ([], False)
        filtered_news = [
            s for s in raw_news
            if s["confidence"] >= self._min_confidence
            and s["disruption_strength"] >= self._min_disruption
        ]
        news_objs: List[SectorSignal] = []
        for raw in filtered_news:
            kws = [kw.lower() for kw in LSE_SECTORS.get(raw["sector"], [])]
            relevant = [
                h for h in headlines
                if any(kw in h.title.lower() or kw in h.summary.lower() for kw in kws)
            ] or headlines[:5]
            news_objs.append(SectorSignal(
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
            ))

        macro = news_objs[0].macro_regime_summary if news_objs else ""

        reddit_sigs: List[RedditSentimentSignal] = []
        reddit_ok = False
        if reddit_posts:
            reddit_sigs, reddit_ok = analyse_reddit_posts(reddit_posts)
            reddit_sigs = [s for s in reddit_sigs if s.bullish_conviction >= REDDIT_CFG.min_reddit_conviction]

        consolidated, consolidator_ok = consolidate_signals(
            news_signals   = news_objs,
            reddit_signals = reddit_sigs,
            macro_regime   = macro,
            min_confidence = self._min_confidence,
        )

        return {
            "signals":        consolidated,
            "llm_succeeded":  consolidator_ok and news_ok,
            "news_signals":   news_objs,
            "reddit_signals": reddit_sigs,
            "macro_regime":   macro,
        }

    # ------------------------------------------------------------------
    # Default console renderer
    # ------------------------------------------------------------------

    @staticmethod
    def _default_logger(signals: List[ConsolidatedSignal]) -> None:
        ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        macro = signals[0].macro_regime_summary if signals else ""

        # Convergence type → display symbol
        _CONV_ICON = {
            "Convergent":  "✦ CONVERGENT",
            "News-Led":    "◈ NEWS-LED",
            "Reddit-Led":  "◉ REDDIT-LED",
            "Divergent":   "⚡ DIVERGENT",
        }

        print("\n" + "=" * 72)
        print(f"  LSE SWING SIGNALS  ·  {ts}")
        print("=" * 72)

        if macro:
            print(f"\n  MACRO REGIME")
            print(f"  {macro}")

        for sig in signals:
            bull_bar  = "#" * int(sig.confidence * 20)
            bear_bar  = "#" * int(sig.bear_case_probability * 20)
            conv_icon = _CONV_ICON.get(sig.convergence_type, sig.convergence_type)

            print(f"\n  {'=' * 68}")
            print(f"  {sig.sector:<26}  BULL {sig.confidence:.0%}  [{bull_bar:<20}]")
            print(f"  {conv_icon:<26}  BEAR {sig.bear_case_probability:.0%}  [{bear_bar:<20}]")
            print(f"  Disruption  : {sig.disruption_type}  (strength {sig.disruption_strength:.0%})")
            print(f"  Impact      : ~{sig.time_to_impact_weeks} week(s)")
            print(f"  Mechanism   : {sig.propagation}")
            print(f"  Rationale   : {sig.rationale}")
            print(f"  Kill switch : {sig.invalidation_risk}")
            print(f"  Convergence : {sig.convergence_note}")
            if sig.retail_thesis and sig.retail_thesis != "No Reddit signal":
                print(f"  Reddit crowd: {sig.retail_thesis}")
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
# CLI — run once and exit
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LSE Sector Scout — one-shot pipeline run")
    parser.add_argument(
        "--skip-reddit", action="store_true",
        help="Skip Reddit collection and analysis (faster; news-only mode)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Use run_cycle_verbose() and print intermediate signal counts",
    )
    args = parser.parse_args()

    scout = SectorScout(skip_reddit=args.skip_reddit)

    if args.verbose:
        result = scout.run_cycle_verbose()
        logger.info(
            "Verbose results: %d consolidated | %d news | %d reddit | macro=%r",
            len(result["signals"]),
            len(result["news_signals"]),
            len(result["reddit_signals"]),
            result["macro_regime"][:80] if result["macro_regime"] else "",
        )
        signals       = result["signals"]
        llm_succeeded = result["llm_succeeded"]
    else:
        signals, llm_succeeded = scout.run_cycle()

    if not llm_succeeded:
        logger.warning(
            "Pipeline did not fully succeed — check logs. "
            "Signals (if any) may be from fallback paths."
        )
        sys.exit(1)

    sys.exit(0 if signals else 1)
