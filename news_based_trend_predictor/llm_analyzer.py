"""
llm_analyzer.py — OpenRouter API client with structured output + reasoning.

Architecture
------------
Calls OpenRouter's /v1/chat/completions endpoint with:
  1. response_format=json_schema or json_object, per model config.
  2. reasoning.effort="high" if the model config enables it.
  3. Model priority fallback across OPENROUTER_MODELS with retry logic.

Model capabilities (json_schema, reasoning) are declared statically in
config.py alongside each model entry. This means zero wasted API calls on
capability probing — critical when running on the free tier.

To add or swap a model: edit OPENROUTER_MODELS in config.py only.
Set use_json_schema and use_reasoning based on the model's OpenRouter page.
No changes needed here.

Prompt philosophy (upgraded for large-model capability)
-------------------------------------------------------
The original prompt was written for a 2B-4B edge model and constrained the
model to a flat, single-pass output format.  Large reasoning models (31B–120B)
benefit from:
  - Explicit multi-stage reasoning scaffolding (macro regime first, then sectors)
  - Historical calibration anchors so confidence scores are grounded
  - Cross-signal coherence — macro themes propagate to correlated sectors
  - Richer output fields that force the model to commit to falsifiable claims
  - Explicit anti-patterns to suppress known failure modes (recency bias, etc.)

Output fields
-------------
  sector, confidence, disruption_type, disruption_strength,
  time_to_impact_weeks, propagation, invalidation_risk, rationale,
  bear_case_probability, key_catalysts, correlated_sectors,
  conviction_drivers, macro_regime_summary

Environment variable required
------------------------------
    OPENROUTER_API_KEY   — your OpenRouter API key
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Dict, List, Optional

import requests
from pydantic import BaseModel, Field, ValidationError, field_validator

from config import (
    DISRUPTION_CATEGORIES,
    LSE_SECTORS,
    ModelConfig,
    OPENROUTER_MODELS,
    OPENROUTER_RETRY_DELAYS,
    SCHEDULER_CFG,
)
from news_fetcher import Headline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic output schema
# ---------------------------------------------------------------------------

_SECTOR_NAMES     = list(LSE_SECTORS.keys())
_DISRUPTION_NAMES = list(DISRUPTION_CATEGORIES.keys())


class SignalItem(BaseModel):
    """A single medium-term swing signal for one LSE sector."""

    sector: str = Field(
        description="The LSE ICB sector name, e.g. 'Energy' or 'Industrials'."
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description=(
            "Calibrated conviction that this sector will show POSITIVE price "
            "movement over 2-6 weeks. Anchor: 0.85+ = semiconductor export-control "
            "level certainty; 0.70 = clear structural signal, some uncertainty; "
            "0.55 = directional lean with meaningful alternative scenarios. "
            "Do not use 0.40-0.54 unless the signal is genuinely borderline."
        ),
    )
    disruption_type: str = Field(
        description=(
            "Category of the structural disruption driving the signal. "
            f"Must be one of: {', '.join(_DISRUPTION_NAMES)}."
        )
    )
    disruption_strength: float = Field(
        ge=0.0, le=1.0,
        description=(
            "Magnitude of the structural break. "
            "0.9+ = paradigm shift (e.g. 2022 energy shock); "
            "0.7 = clear multi-week dislocation; "
            "0.5 = moderate, consensus is still forming."
        ),
    )
    time_to_impact_weeks: int = Field(
        ge=1, le=12,
        description=(
            "Estimated weeks before the market FULLY prices in this disruption. "
            "Be specific: e.g. 3 if an earnings season is 3 weeks away and "
            "the thesis requires EPS confirmation."
        ),
    )
    propagation: str = Field(
        max_length=350,
        description=(
            "One to two sentences (<=40 words) explaining the precise MECHANISM "
            "by which the disruption flows to LSE equities in this sector. "
            "Name the specific revenue lines, cost structures, or re-rating "
            "catalysts affected. Avoid generic descriptions."
        ),
    )
    invalidation_risk: str = Field(
        max_length=250,
        description=(
            "One sentence (<=25 words) naming the single most likely event that "
            "would FALSIFY this thesis, with an estimated probability in brackets, "
            "e.g. '...BoE pause (30% probability)'."
        ),
    )
    rationale: str = Field(
        max_length=350,
        description=(
            "Two sentences (<=40 words total) summarising the overall case: "
            "what the headline cluster signals AND why now is the right entry "
            "window relative to consensus positioning."
        ),
    )
    bear_case_probability: float = Field(
        ge=0.0, le=1.0,
        description=(
            "Estimated probability that the sector moves NEGATIVELY or flat "
            "over the stated time horizon. Should roughly equal 1 - confidence "
            "within 0.10 tolerance."
        ),
    )
    key_catalysts: List[str] = Field(
        default_factory=list,
        description=(
            "List of 1-3 specific, dated or near-term upcoming events that would "
            "CONFIRM the thesis (e.g. 'BoE MPC meeting 8 May', "
            "'AstraZeneca Q2 results', 'OPEC+ output decision mid-June'). "
            "Do not use vague phrases like 'further positive data'."
        ),
    )
    correlated_sectors: List[str] = Field(
        default_factory=list,
        description=(
            "0-2 other LSE sectors secondarily exposed to the SAME disruption "
            "likely to move in the same direction. Must be valid sector names "
            "from the taxonomy. Leave empty if none."
        ),
    )
    conviction_drivers: List[str] = Field(
        default_factory=list,
        description=(
            "List of 2-3 specific headlines or data points from the provided "
            "news batch (paraphrased, not quoted verbatim) that most strongly "
            "support this signal. Cite the source name in brackets."
        ),
    )

    @field_validator("sector")
    @classmethod
    def validate_sector(cls, v: str) -> str:
        if v not in _SECTOR_NAMES:
            raise ValueError(
                f"'{v}' is not a valid LSE sector. "
                f"Choose from: {', '.join(_SECTOR_NAMES)}"
            )
        return v

    @field_validator("disruption_type")
    @classmethod
    def validate_disruption(cls, v: str) -> str:
        if v not in _DISRUPTION_NAMES:
            for name in _DISRUPTION_NAMES:
                if name.lower() == v.lower():
                    return name
            raise ValueError(
                f"'{v}' is not a valid disruption type. "
                f"Choose from: {', '.join(_DISRUPTION_NAMES)}"
            )
        return v

    @field_validator("correlated_sectors", mode="before")
    @classmethod
    def validate_correlated(cls, v) -> List[str]:
        if v is None:
            return []
        valid = [s for s in v if s in _SECTOR_NAMES]
        if len(valid) != len(v):
            dropped = [s for s in v if s not in _SECTOR_NAMES]
            logger.debug("Dropping invalid correlated sector(s): %s", dropped)
        return valid[:2]

    @field_validator("key_catalysts", "conviction_drivers", mode="before")
    @classmethod
    def coerce_list_fields(cls, v) -> List[str]:
        """Accept null/None from models that omit optional list fields."""
        if v is None:
            return []
        return v


class AnalysisOutput(BaseModel):
    """Complete analysis output."""

    macro_regime_summary: str = Field(
        max_length=500,
        description=(
            "2-3 sentence synthesis of the dominant macro regime visible in "
            "today's headlines: characterise the BoE/rates backdrop, "
            "UK growth trajectory, and the single most important global macro "
            "factor currently affecting LSE equities."
        ),
    )
    signals: List[SignalItem] = Field(
        default_factory=list,
        description=(
            "List of sector swing signals sorted by confidence descending. "
            "Include only sectors with confidence >= 0.40. "
            "Return an empty list if no genuine medium-term disruption is identified. "
            "3-6 high-conviction signals is better than 10 marginal ones."
        )
    )


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SECTOR_LIST      = "\n".join(f"  - {s}" for s in _SECTOR_NAMES)
_DISRUPTION_BLOCK = "\n".join(
    f"  [{name}]: {desc}" for name, desc in DISRUPTION_CATEGORIES.items()
)

_SYSTEM_PROMPT = f"""\
You are a senior portfolio manager and quantitative strategist at a 5bn GBP UK \
long/short equity hedge fund, specialising in medium-term (2-6 week) swing \
trades on the London Stock Exchange (LSE).

Your mandate: identify which LSE SECTORS are most likely to show POSITIVE \
price movement over the next 2-6 WEEKS driven by structural market disruptions \
- NOT intraday or daily noise.

======================================================================
STEP 1 - MACRO REGIME ASSESSMENT (do this first, internally)
======================================================================
Before evaluating individual sectors, synthesise the headlines into a macro \
regime characterisation covering:
  (a) UK monetary policy: Is the BoE in a cutting, pausing, or hiking cycle? \
What are gilts/sterling signalling?
  (b) UK growth: Is the data surprising to the upside or downside vs. consensus?
  (c) Global macro overlay: Which single global factor (US rates, China demand, \
commodity prices, geopolitics) is most dominant in the current headline set?

This regime context must inform your sector scoring. A rate-cutting regime \
structurally favours REITs, Utilities, and rate-sensitives. A risk-off \
geopolitical shock favours Industrials/Defence and Energy but pressures \
Consumer Discretionary.

======================================================================
STEP 2 - DISRUPTION IDENTIFICATION
======================================================================
Scan for STRUCTURAL disruptions - not single-day events - of these types:

{_DISRUPTION_BLOCK}

DO NOT score these as signals:
  - A single earnings beat or miss not corroborated by sector-wide data
  - A price move already fully reported in headlines (market has priced it)
  - Intraday volatility without a structural catalyst
  - Analyst upgrades/downgrades without new fundamental information
  - Recency bias: a large story today that is likely to reverse tomorrow

DO score these as signals:
  - Policy or regulatory announcements with multi-week implementation timelines
  - Commodity or FX moves that have NOT yet fed through to equity valuations
  - A cluster of earnings misses/beats implying systematic consensus error
  - Geopolitical escalation changing trade routes or defence budgets
  - Technology inflection with sector-wide adoption consequences

======================================================================
STEP 3 - SECTOR SIGNAL SCORING
======================================================================
For each sector signal, reason through:

  A. MECHANISM: Which specific revenue lines, cost structures, or valuation \
multiples are affected for LSE-listed companies in this sector?

  B. TIMING: Why will the market take 2-6 weeks to fully price this in? \
Is it awaiting earnings confirmation? A central bank meeting? Supply data?

  C. CALIBRATION - score confidence against these anchors:
       0.85+ : semiconductor export-control certainty - structural, multi-month
       0.70  : clear signal, consensus is wrong but proof is 2-4 weeks away
       0.55  : directional lean; alternative scenario has 30-40% probability
       0.40  : marginal; barely above noise threshold

  D. SECOND-ORDER: Does this disruption propagate to 1-2 OTHER sectors via \
supply chains, input costs, or risk-sentiment contagion?

======================================================================
STEP 4 - CROSS-SIGNAL COHERENCE CHECK
======================================================================
Before finalising, review your signal list:
  - Do the signals tell a coherent macro story, or are they contradictory?
  - If you have flagged both defensive (Utilities) and cyclical (Industrials) \
sectors as strong buys, is the macro regime consistent with that?
  - Remove signals where the only evidence is 1-2 headlines without corroboration.

======================================================================
LSE SECTOR TAXONOMY
======================================================================
{_SECTOR_LIST}

======================================================================
OUTPUT REQUIREMENTS
======================================================================
Respond with a SINGLE raw JSON object - no markdown fences, no preamble, \
no commentary outside the JSON. The object must exactly match the schema. \
All string fields must respect their stated word/character limits.

Quality bar: 3-6 high-conviction signals with specific, falsifiable claims is \
far more valuable than 10 vague signals. If the headline batch contains no \
genuine multi-week structural disruption, return an empty signals list - this \
is the correct and calibrated answer.
"""


def _build_messages(headlines: List[Headline]) -> list:
    lines = [f"{i+1}. {h.to_text()}" for i, h in enumerate(headlines)]
    user_text = (
        f"You are analysing {len(headlines)} headlines from the latest collection cycle.\n\n"
        "Work through Steps 1-4 in your system prompt, then emit the JSON.\n\n"
        "Headlines:\n\n" + "\n".join(lines)
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": user_text},
    ]


# ---------------------------------------------------------------------------
# OpenRouter HTTP client
# ---------------------------------------------------------------------------

_OR_URL = "https://openrouter.ai/api/v1/chat/completions"


def _get_headers() -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError(
            "OPENROUTER_API_KEY environment variable is not set. "
            "Create a free account at https://openrouter.ai, generate an API key, "
            "and set it before running: export OPENROUTER_API_KEY=sk-or-..."
        )
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://github.com/your-org/sector-scout",
        "X-Title":       "LSE Sector Scout",
    }


def _build_payload(messages: list, cfg: ModelConfig) -> dict:
    """
    Build the request payload using the capability flags declared in config.
    One payload per model, built correctly the first time — no probing.
    """
    payload: dict = {
        "model":       cfg.model,
        "messages":    messages,
        "temperature": 0.35,
        "max_tokens":  2048,
    }

    if cfg.use_json_schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name":   "lse_sector_analysis",
                "strict": False,
                "schema": AnalysisOutput.model_json_schema(),
            },
        }
    else:
        # Plain JSON mode — all models support this; output shaped by prompt alone
        payload["response_format"] = {"type": "json_object"}

    if cfg.use_reasoning:
        payload["reasoning"] = {"effort": "high", "exclude": True}

    return payload


def _call_openrouter(messages: list, cfg: ModelConfig) -> str:
    """
    Single synchronous call to OpenRouter.
    Returns the raw content string from the first choice.
    Raises requests.HTTPError on 4xx / 5xx.
    Raises requests.Timeout on timeout.
    """
    payload = _build_payload(messages, cfg)
    resp = requests.post(
        _OR_URL,
        headers=_get_headers(),
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(raw: str) -> str:
    """
    Best-effort extraction of a JSON object from a raw model response.
    Handles markdown fences, <think> blocks, and JSON embedded in prose.
    """
    # Strip markdown fences
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

    # Strip <think>...</think> blocks leaked by some reasoning models
    clean = re.sub(r"<think>[\s\S]*?</think>", "", clean, flags=re.IGNORECASE).strip()

    if clean.startswith("{"):
        return clean

    # Extract outermost {...} from prose-wrapped output
    match = re.search(r"\{[\s\S]+\}", clean)
    if match:
        logger.debug("JSON extracted from prose-wrapped response.")
        return match.group(0)

    return clean


# ---------------------------------------------------------------------------
# Retry + model fallback logic
# ---------------------------------------------------------------------------

def _invoke_with_fallback(messages: list) -> AnalysisOutput:
    """
    Try each ModelConfig in OPENROUTER_MODELS in priority order.
    Within each model, retry on transient errors (429, 502-504, timeout).
    Permanent errors (400, 401, ValidationError) skip immediately to the next model.
    Returns a validated AnalysisOutput or raises RuntimeError if all fail.
    """
    for cfg in OPENROUTER_MODELS:
        raw: Optional[str] = None

        for attempt, delay in enumerate(OPENROUTER_RETRY_DELAYS, start=1):
            try:
                logger.info(
                    "OpenRouter: trying %s (attempt %d/%d, json_schema=%s, reasoning=%s) ...",
                    cfg.model, attempt, len(OPENROUTER_RETRY_DELAYS),
                    cfg.use_json_schema, cfg.use_reasoning,
                )
                raw = _call_openrouter(messages, cfg)
                clean = _extract_json(raw)
                result = AnalysisOutput.model_validate_json(clean)
                logger.info("+ %s — %d signals validated.", cfg.model, len(result.signals))
                return result

            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0

                if status == 429:
                    logger.warning("%s: rate limited (429) — sleeping %ds.", cfg.model, delay)
                    time.sleep(delay)

                elif status in (502, 503, 504):
                    logger.warning("%s: provider error (%d) — sleeping 10s.", cfg.model, status)
                    time.sleep(10)

                else:
                    # 400, 401, 422 etc. — permanent; move on immediately
                    logger.error(
                        "%s: HTTP %d — skipping to next model.\n%s",
                        cfg.model, status,
                        (exc.response.text or "")[:400],
                    )
                    break

            except (ValidationError, ValueError) as exc:
                logger.warning("%s: validation failed (%s).", cfg.model, exc)
                # One salvage attempt: pull outermost JSON object in case the
                # model wrapped its response in prose despite json_object mode
                if raw:
                    salvage = re.search(r"\{[\s\S]+\}", raw)
                    if salvage:
                        try:
                            result = AnalysisOutput.model_validate_json(salvage.group(0))
                            logger.info(
                                "+ %s — salvage succeeded, %d signals.", cfg.model, len(result.signals)
                            )
                            return result
                        except Exception as salvage_exc:
                            logger.warning("%s: salvage also failed (%s).", cfg.model, salvage_exc)
                logger.error("%s: skipping to next model.", cfg.model)
                break

            except requests.Timeout:
                logger.warning(
                    "%s: timeout (attempt %d/%d) — sleeping %ds.",
                    cfg.model, attempt, len(OPENROUTER_RETRY_DELAYS), delay,
                )
                time.sleep(delay)

            except Exception as exc:
                logger.error("%s: unexpected error: %s — skipping.", cfg.model, exc)
                break

        else:
            logger.warning("%s: all retries exhausted — moving to next model.", cfg.model)

    raise RuntimeError("All OpenRouter models exhausted without a valid response.")


# ---------------------------------------------------------------------------
# Keyword-scoring fallback
# ---------------------------------------------------------------------------

_DISRUPTION_WORDS = {
    "tariff", "sanction", "ban", "restriction", "export control",
    "legislation", "regulation", "rate hike", "rate cut", "gilt",
    "inflation", "geopolit", "conflict", "war", "shortage",
    "acquisition", "merger", "bid", "takeover", "breakthrough",
    "approval", "patent", "miss", "warning", "profit alert",
}
_POSITIVE_WORDS = {
    "rise", "surge", "jump", "gain", "up", "boost", "record",
    "beat", "strong", "growth", "upgrade", "outperform", "profit",
    "revenue", "rally", "rebound", "recover",
}


def _keyword_fallback(headlines: List[Headline]) -> List[Dict]:
    logger.warning("Using keyword heuristic fallback.")
    all_text = " ".join(h.title.lower() + " " + h.summary.lower() for h in headlines)
    results  = []
    for sector, keywords in LSE_SECTORS.items():
        kw_hits  = sum(1 for kw in keywords if kw.lower() in all_text)
        if kw_hits == 0:
            continue
        dis_hits = sum(1 for dw in _DISRUPTION_WORDS if dw in all_text)
        pos_hits = sum(1 for pw in _POSITIVE_WORDS if pw in all_text)
        confidence = min(0.40 + (kw_hits/12)*0.35 + (dis_hits/15)*0.15 + (pos_hits/20)*0.10, 0.85)
        results.append({
            "sector":               sector,
            "confidence":           round(confidence, 3),
            "disruption_type":      "Regulatory / Policy Shift",
            "disruption_strength":  round(min(dis_hits / 10, 1.0), 3),
            "time_to_impact_weeks": 3,
            "propagation":          f"Keyword signal: {kw_hits} sector matches (heuristic fallback).",
            "invalidation_risk":    "Heuristic - validate manually.",
            "rationale":            f"Keyword-based: {kw_hits} sector hits in headlines.",
            "bear_case_probability": round(1.0 - confidence, 3),
            "key_catalysts":        ["Manual validation required"],
            "correlated_sectors":   [],
            "conviction_drivers":   ["Heuristic fallback - no LLM analysis available"],
            "macro_regime_summary": "Heuristic fallback - no LLM macro analysis available.",
        })
    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results[:5]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyse_headlines(headlines: List[Headline]) -> List[Dict]:
    """
    Call OpenRouter with the supplied headlines and return a validated list
    of swing signal dicts.

    Falls back to keyword heuristic if all OpenRouter models fail.
    """
    if not headlines:
        logger.warning("analyse_headlines called with empty list.")
        return []

    messages = _build_messages(headlines)
    logger.info("Invoking OpenRouter on %d headlines ...", len(headlines))

    try:
        result = _invoke_with_fallback(messages)
    except RuntimeError as exc:
        logger.error("%s - using keyword fallback.", exc)
        return _keyword_fallback(headlines)

    if result.macro_regime_summary:
        logger.info("Macro regime: %s", result.macro_regime_summary)

    signals = sorted(result.signals, key=lambda s: s.confidence, reverse=True)
    logger.info("Returning %d validated signals.", len(signals))

    return [
        {
            "sector":               sig.sector,
            "confidence":           round(sig.confidence, 4),
            "disruption_type":      sig.disruption_type,
            "disruption_strength":  round(sig.disruption_strength, 4),
            "time_to_impact_weeks": sig.time_to_impact_weeks,
            "propagation":          sig.propagation,
            "invalidation_risk":    sig.invalidation_risk,
            "rationale":            sig.rationale,
            "bear_case_probability": round(sig.bear_case_probability, 4),
            "key_catalysts":        sig.key_catalysts,
            "correlated_sectors":   sig.correlated_sectors,
            "conviction_drivers":   sig.conviction_drivers,
            "macro_regime_summary": result.macro_regime_summary,
        }
        for sig in signals
    ]
