"""
consolidator.py — Second LLM pass: news signals + Reddit sentiment → final signals.

Architecture
------------
Takes two independently scored inputs:
  1. List[SectorSignal]         — from the news LLM pass (llm_analyzer.py)
  2. List[RedditSentimentSignal] — from the Reddit LLM pass (reddit_analyzer.py)

The consolidator LLM is asked to:
  a) Identify CONVERGENCE: news thesis + retail conviction pointing in the same
     direction → confidence boost.  These are the highest-quality signals.
  b) Identify DIVERGENCE: news signal exists but retail is absent or bearish →
     flag for caution.  May still be valid (retail often lags) but warrants
     a lower consolidated confidence.
  c) Identify REDDIT-ONLY: strong retail conviction in a sector where the news
     pass found nothing.  Surface only if signal_quality >= Discussion/DD and
     bullish_conviction >= 0.60.  Could be early positioning ahead of catalyst.

Output: List[ConsolidatedSignal] — the final signals emitted by the pipeline.

The consolidated signals replace (not supplement) the raw news signals in the
final email / S3 output.  The news signals and Reddit signals are stored
separately in the S3 payload for audit purposes.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import requests

from config import (
    LSE_SECTORS,
    DISRUPTION_CATEGORIES,
    OPENROUTER_RETRY_DELAYS,
    load_openrouter_models,
)
from llm_analyzer import _load_api_key, _extract_and_decode
from reddit_analyzer import RedditSentimentSignal

logger = logging.getLogger(__name__)

_SECTOR_NAMES     = list(LSE_SECTORS.keys())
_DISRUPTION_NAMES = list(DISRUPTION_CATEGORIES.keys())

# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

_CONSOLIDATED_SCHEMA = """\
{"macro": {"regime": "<1 sentence: BoE stance + dominant global factor>"},
 "signals": [
  {"sector": "<exact from SECTORS>",
   "confidence": 0.0,
   "disruption_type": "<exact from DISRUPTIONS>",
   "disruption_strength": 0.0,
   "time_to_impact_weeks": 0,
   "bear_case_probability": 0.0,
   "convergence_type": "<Convergent|News-Led|Reddit-Led|Divergent>",
   "convergence_note": "<≤25w: why news+reddit agree or disagree>",
   "propagation": "<≤25w: mechanism>",
   "invalidation_risk": "<≤20w: kill-switch + (prob%)>",
   "rationale": "<≤30w: consolidated case>",
   "retail_thesis": "<≤20w: what the Reddit crowd believes, or 'No Reddit signal'>",
   "key_catalysts": ["<near-term confirming event>"],
   "correlated_sectors": ["<0-2 exact sector names>"],
   "conviction_drivers": ["<≤15w fact [Source: News|Reddit]>",
                          "<≤15w fact [Source: News|Reddit]>",
                          "<≤15w fact [Source: News|Reddit]>"]
  }
 ]
}"""

_SYSTEM_PROMPT = f"""\
You are a senior LSE portfolio manager reviewing two independently scored
intelligence feeds: (A) a structured news analysis and (B) retail trader
sentiment from Reddit.

## ROLE
Produce a CONSOLIDATED set of swing trading signals (2-6 week horizon) for
LSE-listed sectors by merging both feeds and labelling the degree of agreement.

## STEP 1 — CONVERGENCE CLASSIFICATION
Apply exactly one label per signal:

  Convergent  — news thesis and Reddit conviction point in the same direction.
                Boost confidence by up to +0.10 relative to the news score.
                These are your highest-conviction signals.

  News-Led    — strong news thesis; Reddit absent or neutral.
                Keep confidence at the news score, or discount by −0.05 if the
                thesis is macro/policy-driven (retail wouldn't know yet).

  Reddit-Led  — strong Reddit DD/Discussion; news pass found nothing.
                Only surface if bullish_conviction ≥ 0.60 AND signal_quality
                is DD or Discussion. Set confidence = bullish_conviction × 0.85.

  Divergent   — news bullish, Reddit explicitly bearish (not merely absent).
                Discount confidence by −0.15. Note the divergence clearly.

## STEP 2 — CONFIDENCE ANCHORS
  0.85+  structural certainty (multi-month)
  0.70   clear thesis, confirming proof expected 2-4w
  0.55   directional lean, ~35% bear case
  0.35-0.50  marginal but worth surfacing

## STEP 3 — SOURCE DIVERSITY ADJUSTMENT
Each news signal carries a source_diversity score (0-1).
  diversity ≥ 0.7 → no cap; score to your natural ceiling
  diversity 0.4-0.69 → cap confidence at 0.75
  diversity < 0.4  → cap confidence at 0.60

## STEP 4 — OUTPUT RULES
  • Target 3-7 signals. Quality > quantity.
  • An empty signals array is only valid if both inputs are empty or pure noise.
  • Every conviction_driver must be ≤15 words and end with [Source: News] or
    [Source: Reddit].

## REFERENCE LISTS
SECTORS (exact spelling):
{" | ".join(_SECTOR_NAMES)}

DISRUPTION TYPES (exact spelling):
{" | ".join(_DISRUPTION_NAMES)}

## OUTPUT FORMAT
Respond with ONLY a valid JSON object — no markdown fences, no preamble,
no trailing commentary.
Shape:
{_CONSOLIDATED_SCHEMA}
"""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _format_news_signals(news_signals: list) -> str:
    """Compact text representation of news-pass signals for the prompt."""
    if not news_signals:
        return "No news signals were produced."
    lines = ["=== NEWS ANALYSIS SIGNALS ==="]
    for i, s in enumerate(news_signals, 1):
        # Accept both SectorSignal objects and plain dicts
        if hasattr(s, "to_dict"):
            d = s.to_dict()
        else:
            d = s
        diversity_str = f"{d.get('source_diversity', 0.0):.2f}" if "source_diversity" in d else "n/a"
        lines.append(
            f"{i}. Sector: {d['sector']}\n"
            f"   Confidence: {d['confidence']:.0%}  Bear: {d['bear_case_probability']:.0%}"
            f"  Source diversity: {diversity_str}\n"
            f"   Disruption: {d['disruption_type']}  Strength: {d['disruption_strength']:.0%}\n"
            f"   Impact: ~{d['time_to_impact_weeks']}w\n"
            f"   Rationale: {d['rationale']}\n"
            f"   Mechanism: {d['propagation']}\n"
            f"   Kill switch: {d['invalidation_risk']}\n"
            f"   Evidence: {'; '.join(d.get('conviction_drivers', []))}"
        )
    return "\n".join(lines)


def _format_reddit_signals(reddit_signals: List[RedditSentimentSignal]) -> str:
    """Compact text representation of Reddit sentiment signals for the prompt."""
    if not reddit_signals:
        return "No Reddit sentiment signals were produced."
    lines = ["=== REDDIT SENTIMENT SIGNALS ==="]
    for i, s in enumerate(reddit_signals, 1):
        tickers = ", ".join(s.representative_tickers) if s.representative_tickers else "not specified"
        lines.append(
            f"{i}. Sector: {s.sector}\n"
            f"   Bullish conviction: {s.bullish_conviction:.0%}  Quality: {s.signal_quality}\n"
            f"   Post count: {s.post_count}  Avg score: {s.avg_score:.0f}\n"
            f"   Tickers mentioned: {tickers}\n"
            f"   Retail thesis: {s.retail_thesis}\n"
            f"   Crowd risk: {s.crowd_risk}"
        )
    return "\n".join(lines)


def _build_consolidation_messages(
    news_signals: list,
    reddit_signals: List[RedditSentimentSignal],
    macro_regime: str,
) -> list:
    news_text   = _format_news_signals(news_signals)
    reddit_text = _format_reddit_signals(reddit_signals)
    macro_text  = f"Current macro regime (from news pass): {macro_regime}" if macro_regime else ""

    user_text = "\n\n".join(filter(None, [macro_text, news_text, reddit_text]))
    user_text += "\n\nConsolidate these signals and emit the JSON object."

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": user_text},
    ]


# ---------------------------------------------------------------------------
# OpenRouter client (same pattern as llm_analyzer)
# ---------------------------------------------------------------------------

_OR_URL = "https://openrouter.ai/api/v1/chat/completions"


def _get_headers() -> dict:
    return {
        "Authorization": f"Bearer {_load_api_key()}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://github.com/your-org/sector-scout",
        "X-Title":       "LSE Sector Scout - Consolidator",
    }


def _call_openrouter(messages: list, model_chunk: list) -> tuple:
    payload = {
        "model":       model_chunk[0],
        "messages":    messages,
        "temperature": 0.30,
        "max_tokens":  4096,
        "reasoning":   {"effort": "high", "exclude": True},
        "response_format": {"type": "json_object"},
        "models": model_chunk,
        "route":  "fallback",
    }
    resp = requests.post(_OR_URL, headers=_get_headers(), data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), timeout=180)
    resp.raise_for_status()
    data   = resp.json()
    choice = data["choices"][0]
    return choice["message"]["content"], choice.get("finish_reason", "unknown"), data.get("model", model_chunk[0])


def _invoke_with_fallback(messages: list) -> tuple[Optional[dict], Optional[str]]:
    models = load_openrouter_models()
    chunks = [models[i:i+3] for i in range(0, len(models), 3)]

    for chunk_idx, chunk in enumerate(chunks, start=1):
        logger.info("Consolidator: chunk %d/%d — %s", chunk_idx, len(chunks), " → ".join(chunk))
        for attempt, delay in enumerate(OPENROUTER_RETRY_DELAYS, start=1):
            try:
                raw, finish_reason, model_used = _call_openrouter(messages, chunk)
                decoded = _extract_and_decode(raw, finish_reason)
                if decoded is not None:
                    logger.info("Consolidator: served by %s.", model_used)
                    return decoded, model_used
                logger.error("Consolidator: JSON decode failed on chunk %d.", chunk_idx)
                break

            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status in (429, 502, 503, 504):
                    logger.warning("Consolidator chunk %d: HTTP %d — sleeping %ds.", chunk_idx, status, delay)
                    time.sleep(delay)
                else:
                    logger.error("Consolidator chunk %d: HTTP %d non-retryable.", chunk_idx, status)
                    break

            except requests.Timeout:
                logger.warning("Consolidator chunk %d: timeout (attempt %d).", chunk_idx, attempt)
                time.sleep(delay)

            except Exception as exc:
                logger.error("Consolidator chunk %d: unexpected: %s.", chunk_idx, exc)
                break
        else:
            logger.warning("Consolidator chunk %d: all retries exhausted.", chunk_idx)

    logger.error("Consolidator: all chunks exhausted.")
    return None, None


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

@dataclass
class ConsolidatedSignal:
    sector:               str
    confidence:           float
    disruption_type:      str
    disruption_strength:  float
    time_to_impact_weeks: int
    bear_case_probability: float
    convergence_type:     str     # Convergent | News-Led | Reddit-Led | Divergent
    convergence_note:     str
    propagation:          str
    invalidation_risk:    str
    rationale:            str
    retail_thesis:        str
    key_catalysts:        List[str]
    correlated_sectors:   List[str]
    conviction_drivers:   List[str]
    macro_regime_summary: str
    source_diversity:     float = 0.0   # 0-1; inherited from news pass
    timestamp:            datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return (
            f"ConsolidatedSignal(sector={self.sector!r}, "
            f"conf={self.confidence:.0%}, "
            f"type={self.convergence_type!r})"
        )

    def to_dict(self) -> dict:
        return {
            "sector":               self.sector,
            "confidence":           self.confidence,
            "disruption_type":      self.disruption_type,
            "disruption_strength":  self.disruption_strength,
            "time_to_impact_weeks": self.time_to_impact_weeks,
            "bear_case_probability": self.bear_case_probability,
            "convergence_type":     self.convergence_type,
            "convergence_note":     self.convergence_note,
            "propagation":          self.propagation,
            "invalidation_risk":    self.invalidation_risk,
            "rationale":            self.rationale,
            "retail_thesis":        self.retail_thesis,
            "key_catalysts":        self.key_catalysts,
            "correlated_sectors":   self.correlated_sectors,
            "conviction_drivers":   self.conviction_drivers,
            "macro_regime_summary": self.macro_regime_summary,
            "source_diversity":     self.source_diversity,
            "timestamp":            self.timestamp.isoformat(),
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_consolidated(raw: dict, macro: str) -> Optional[ConsolidatedSignal]:
    sector = raw.get("sector", "")
    if sector not in _SECTOR_NAMES:
        match = next((s for s in _SECTOR_NAMES if s.lower() == sector.lower()), None)
        if match:
            sector = match
        else:
            logger.warning("Consolidated signal: unknown sector %r — dropping.", sector)
            return None

    disruption = raw.get("disruption_type", "")
    if disruption not in _DISRUPTION_NAMES:
        match = next((d for d in _DISRUPTION_NAMES if d.lower() == disruption.lower()), None)
        disruption = match or "Regulatory / Policy Shift"

    for key in ("confidence", "disruption_strength", "bear_case_probability"):
        val = raw.get(key)
        if not isinstance(val, (int, float)):
            logger.warning("Consolidated signal %r: non-numeric %r — dropping.", sector, key)
            return None
        raw[key] = max(0.0, min(1.0, float(val)))

    time_w = raw.get("time_to_impact_weeks", 3)
    try:
        time_w = max(1, min(12, int(time_w)))
    except (TypeError, ValueError):
        time_w = 3

    for key in ("key_catalysts", "correlated_sectors", "conviction_drivers"):
        if not isinstance(raw.get(key), list):
            raw[key] = []

    raw["correlated_sectors"] = [
        s for s in raw.get("correlated_sectors", []) if s in _SECTOR_NAMES
    ][:2]

    valid_types = {"Convergent", "News-Led", "Reddit-Led", "Divergent"}
    conv_type = raw.get("convergence_type", "News-Led")
    if conv_type not in valid_types:
        conv_type = "News-Led"

    return ConsolidatedSignal(
        sector               = sector,
        confidence           = round(raw["confidence"], 4),
        disruption_type      = disruption,
        disruption_strength  = round(raw["disruption_strength"], 4),
        time_to_impact_weeks = time_w,
        bear_case_probability = round(raw["bear_case_probability"], 4),
        convergence_type     = conv_type,
        convergence_note     = raw.get("convergence_note", "")[:300],
        propagation          = raw.get("propagation", ""),
        invalidation_risk    = raw.get("invalidation_risk", ""),
        rationale            = raw.get("rationale", ""),
        retail_thesis        = raw.get("retail_thesis", "No Reddit signal"),
        key_catalysts        = raw.get("key_catalysts", []),
        correlated_sectors   = raw.get("correlated_sectors", []),
        conviction_drivers   = raw.get("conviction_drivers", []),
        macro_regime_summary = macro,
        source_diversity     = round(float(raw.get("source_diversity", 0.0)), 3),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def consolidate_signals(
    news_signals: list,
    reddit_signals: List[RedditSentimentSignal],
    macro_regime: str = "",
    min_confidence: float = 0.50,
) -> tuple[List[ConsolidatedSignal], bool]:
    """
    Merge news-pass signals with Reddit sentiment signals via a second LLM call.

    Returns (consolidated_signals, llm_succeeded).

    Falls back gracefully:
    - If the consolidation LLM fails but news signals are available,
      the news signals are wrapped as ConsolidatedSignal objects
      (convergence_type="News-Led", retail_thesis="No Reddit signal")
      so the pipeline can still emit output.
    - If both news signals and Reddit signals are empty, returns ([], False).
    """
    has_news   = bool(news_signals)
    has_reddit = bool(reddit_signals)

    if not has_news and not has_reddit:
        logger.warning("Consolidator: both inputs are empty — nothing to consolidate.")
        return [], False

    messages = _build_consolidation_messages(news_signals, reddit_signals, macro_regime)
    logger.info(
        "Consolidator: merging %d news signals + %d Reddit signals ...",
        len(news_signals), len(reddit_signals),
    )

    decoded, model_used = _invoke_with_fallback(messages)

    if decoded is None:
        logger.error(
            "Consolidator: LLM failed — falling back to raw news signals (wrapped)."
        )
        if not has_news:
            return [], False
        return _wrap_news_signals_as_consolidated(news_signals, macro_regime), False

    # Extract macro from consolidated response (may refine the news-pass macro)
    macro_obj = decoded.get("macro", {})
    consolidated_macro = (
        macro_obj.get("regime", macro_regime)
        if isinstance(macro_obj, dict)
        else macro_regime
    )
    if consolidated_macro:
        logger.info("Consolidated macro: %s", consolidated_macro)

    raw_signals = decoded.get("signals", [])
    if not isinstance(raw_signals, list):
        logger.warning("Consolidator: 'signals' is not a list — falling back to news signals.")
        return _wrap_news_signals_as_consolidated(news_signals, macro_regime), False

    signals: List[ConsolidatedSignal] = []
    for raw in raw_signals:
        sig = _validate_consolidated(raw, consolidated_macro or macro_regime)
        if sig and sig.confidence >= min_confidence:
            signals.append(sig)

    signals.sort(key=lambda s: s.confidence, reverse=True)
    logger.info(
        "Consolidator: %d consolidated signals (model=%s, llm_succeeded=True).",
        len(signals), model_used,
    )
    return signals, True


def _wrap_news_signals_as_consolidated(
    news_signals: list, macro_regime: str
) -> List[ConsolidatedSignal]:
    """
    Fallback: convert raw news-pass signals into ConsolidatedSignal objects
    with convergence_type='News-Led' and no retail data.  Used when the
    consolidation LLM call fails so the pipeline still emits usable output.
    """
    wrapped = []
    for s in news_signals:
        d = s.to_dict() if hasattr(s, "to_dict") else s
        try:
            wrapped.append(ConsolidatedSignal(
                sector               = d["sector"],
                confidence           = d["confidence"],
                disruption_type      = d["disruption_type"],
                disruption_strength  = d["disruption_strength"],
                time_to_impact_weeks = d["time_to_impact_weeks"],
                bear_case_probability = d["bear_case_probability"],
                convergence_type     = "News-Led",
                convergence_note     = "Consolidator LLM failed — using raw news signal.",
                propagation          = d.get("propagation", ""),
                invalidation_risk    = d.get("invalidation_risk", ""),
                rationale            = d.get("rationale", ""),
                retail_thesis        = "No Reddit signal (consolidator fallback)",
                key_catalysts        = d.get("key_catalysts", []),
                correlated_sectors   = d.get("correlated_sectors", []),
                conviction_drivers   = d.get("conviction_drivers", []),
                macro_regime_summary = macro_regime,
                source_diversity     = round(float(d.get("source_diversity", 0.0)), 3),
            ))
        except (KeyError, TypeError) as exc:
            logger.warning("_wrap_news_signals: could not wrap signal: %s", exc)
    return wrapped
