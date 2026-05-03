"""
llm_analyzer.py — OpenRouter API client with structured output + reasoning.

Architecture change from original
-----------------------------------
The original used LangChain + a locally-loaded Gemma 4 E2B-it (GPU required).
This version calls OpenRouter's /v1/chat/completions endpoint, which:

  1. Hosts the model remotely — no GPU, no torch, no transformers.
  2. Supports response_format=json_schema for enforced structured output.
  3. Supports reasoning.effort for chain-of-thought thinking before output.
  4. Is OpenAI-API-compatible — standard requests.post(), no special SDK needed.

The Pydantic schemas (SignalItem, AnalysisOutput), field validators,
prompt construction, keyword fallback, and public analyse_headlines()
signature are all unchanged from the original.

Environment variable required
------------------------------
    OPENROUTER_API_KEY   — your OpenRouter API key (free account is sufficient)

Model priority / fallback
--------------------------
Models are tried in the order defined in config.OPENROUTER_MODELS.
Within each model, up to len(OPENROUTER_RETRY_DELAYS) retries are attempted
on transient errors (429 rate-limit, 502/503/504 provider errors, timeouts).
Validation errors and permanent HTTP errors (400, 401, 422) skip immediately
to the next model.
If all models are exhausted the keyword heuristic fallback is used.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Dict, List

import requests
from pydantic import BaseModel, Field, ValidationError, field_validator

from config import (
    DISRUPTION_CATEGORIES,
    LSE_SECTORS,
    OPENROUTER_MODELS,
    OPENROUTER_RETRY_DELAYS,
    SCHEDULER_CFG,
)
from news_fetcher import Headline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic output schema  (unchanged from original)
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
        description="Conviction that this sector will show positive movement. 0=none, 1=certain.",
    )
    disruption_type: str = Field(
        description=(
            "Category of the structural disruption driving the signal. "
            f"Must be one of: {', '.join(_DISRUPTION_NAMES)}."
        )
    )
    disruption_strength: float = Field(
        ge=0.0, le=1.0,
        description="Magnitude of the structural break. 0=noise, 1=paradigm shift.",
    )
    time_to_impact_weeks: int = Field(
        ge=1, le=12,
        description="Estimated weeks before the market fully prices in this disruption.",
    )
    propagation: str = Field(
        max_length=300,
        description=(
            "One sentence (≤30 words) explaining HOW the disruption flows through "
            "to LSE-listed equities in this sector."
        ),
    )
    invalidation_risk: str = Field(
        max_length=200,
        description="One sentence (≤20 words) — what single event would kill this thesis.",
    )
    rationale: str = Field(
        max_length=300,
        description="One sentence (≤30 words) — overall case summary.",
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


class AnalysisOutput(BaseModel):
    """Complete analysis output — a ranked list of sector swing signals."""

    signals: List[SignalItem] = Field(
        description=(
            "List of sector swing signals sorted by confidence descending. "
            "Include only sectors with confidence >= 0.4. "
            "Return an empty list if no genuine medium-term disruption is identified."
        )
    )


# ---------------------------------------------------------------------------
# Prompt construction  (unchanged from original)
# ---------------------------------------------------------------------------

_SECTOR_LIST      = "\n".join(f"  - {s}" for s in _SECTOR_NAMES)
_DISRUPTION_BLOCK = "\n".join(
    f"  [{name}]: {desc}" for name, desc in DISRUPTION_CATEGORIES.items()
)

_SYSTEM_PROMPT = f"""\
You are a senior quantitative strategist at a UK hedge fund specialising in
medium-term swing trades on the London Stock Exchange (LSE).

Your task: identify which LSE sectors are most likely to show POSITIVE price
movement over the next 2–6 WEEKS due to structural market disruptions —
NOT short-term daily noise.

Think about disruptions like the semiconductor export-control escalation that
moved an entire sector for weeks. Ignore one-day earnings beats unless they
signal a systematic sector-wide re-rating.

DISRUPTION TYPES:
{_DISRUPTION_BLOCK}

LSE SECTORS:
{_SECTOR_LIST}

For each signal, reason through:
1. WHAT is the disruption and what type is it?
2. HOW does it propagate to LSE equities — which revenue lines, cost
   structures, or re-rating catalysts are affected?
3. WHEN will the full repricing occur? (weeks estimate)
4. WHAT single event would invalidate the thesis?

Only include sectors with confidence >= 0.4.
Return an empty signals list if no genuine multi-week disruption is present.
You MUST respond with a single raw JSON object — no markdown fences, no preamble.
The object must match this exact schema:
{{
  "signals": [
    {{
      "sector":               "<one of the LSE sectors listed above>",
      "confidence":           <float 0.0–1.0>,
      "disruption_type":      "<one of the disruption types listed above>",
      "disruption_strength":  <float 0.0–1.0>,
      "time_to_impact_weeks": <integer 1–12>,
      "propagation":          "<one sentence, ≤30 words>",
      "invalidation_risk":    "<one sentence, ≤20 words>",
      "rationale":            "<one sentence, ≤30 words>"
    }}
  ]
}}
"""


def _build_messages(headlines: List[Headline]) -> list:
    lines     = [f"{i+1}. {h.to_text()}" for i, h in enumerate(headlines)]
    user_text = "Recent headlines:\n\n" + "\n".join(lines)
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


def _call_openrouter(messages: list, model: str) -> str:
    """
    Single synchronous call to OpenRouter.
    Returns the raw content string from the first choice.
    Raises requests.HTTPError on 4xx / 5xx.
    Raises requests.Timeout on timeout.
    """
    payload = {
        "model":       model,
        "messages":    messages,
        "temperature": 0.4,
        "max_tokens":  1024,

        # ── Structured output ─────────────────────────────────────────────
        # Passes the Pydantic schema directly so the model is constrained
        # to return JSON matching our exact field definitions.
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name":   "sector_analysis",
                "strict": True,
                "schema": AnalysisOutput.model_json_schema(),
            },
        },

        # ── Reasoning / thinking ──────────────────────────────────────────
        # "medium" effort (~50% of max_tokens allocated to internal CoT).
        # exclude=True means the model thinks before answering but does NOT
        # return the thinking tokens in the response — saves output tokens
        # and avoids any need to strip <think> blocks before JSON parsing.
        "reasoning": {
            "effort":  "medium",
            "exclude": True,
        },
    }

    resp = requests.post(
        _OR_URL,
        headers=_get_headers(),
        json=payload,
        timeout=120,   # free-tier requests can queue; 2 min is safe
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Retry + model fallback logic
# ---------------------------------------------------------------------------

def _invoke_with_fallback(messages: list) -> AnalysisOutput:
    """
    Try each model in OPENROUTER_MODELS in priority order.
    Within each model, retry on transient errors using OPENROUTER_RETRY_DELAYS.
    Returns a validated AnalysisOutput or raises RuntimeError if all fail.
    """
    for model in OPENROUTER_MODELS:
        for attempt, delay in enumerate(OPENROUTER_RETRY_DELAYS, start=1):
            try:
                logger.info("OpenRouter: trying %s (attempt %d/%d) …",
                            model, attempt, len(OPENROUTER_RETRY_DELAYS))
                raw = _call_openrouter(messages, model)

                # Strip markdown fences in case the model ignores json_schema
                clean = re.sub(
                    r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE
                ).strip()

                result = AnalysisOutput.model_validate_json(clean)
                logger.info("✓ %s — %d signals validated.", model, len(result.signals))
                return result

            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0

                if status == 429:
                    logger.warning("%s: rate limited (429) — sleeping %ds.", model, delay)
                    time.sleep(delay)

                elif status in (502, 503, 504):
                    logger.warning("%s: provider error (%d) — sleeping 10s.", model, status)
                    time.sleep(10)

                else:
                    # 400 / 401 / 422 — permanent error for this model; skip it
                    logger.error("%s: HTTP %d — skipping to next model.", model, status)
                    break

            except (ValidationError, ValueError) as exc:
                # Model returned valid HTTP 200 but malformed / non-conforming JSON.
                # No point retrying the same model — move on.
                logger.warning("%s: Pydantic validation failed (%s) — skipping.", model, exc)
                break

            except requests.Timeout:
                logger.warning("%s: timeout (attempt %d/%d) — sleeping %ds.",
                               model, attempt, len(OPENROUTER_RETRY_DELAYS), delay)
                time.sleep(delay)

            except Exception as exc:
                logger.error("%s: unexpected error: %s — skipping.", model, exc)
                break

        else:
            # Exhausted all retries for this model without a break — try next
            logger.warning("%s: all retries exhausted — moving to next model.", model)

    raise RuntimeError(
        "All OpenRouter models exhausted without a valid response."
    )


# ---------------------------------------------------------------------------
# Keyword-scoring fallback  (unchanged from original)
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
            "invalidation_risk":    "Heuristic — validate manually.",
            "rationale":            f"Keyword-based: {kw_hits} sector hits in headlines.",
        })
    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results[:5]


# ---------------------------------------------------------------------------
# Public API  (signature unchanged from original)
# ---------------------------------------------------------------------------

def analyse_headlines(headlines: List[Headline]) -> List[Dict]:
    """
    Call OpenRouter with the supplied headlines and return a validated list
    of swing signal dicts.

    Each dict contains:
        sector, confidence, disruption_type, disruption_strength,
        time_to_impact_weeks, propagation, invalidation_risk, rationale

    Falls back to keyword heuristic if all OpenRouter models fail.
    """
    if not headlines:
        logger.warning("analyse_headlines called with empty list.")
        return []

    messages = _build_messages(headlines)
    logger.info("Invoking OpenRouter on %d headlines …", len(headlines))

    try:
        result = _invoke_with_fallback(messages)
    except RuntimeError as exc:
        logger.error("%s — using keyword fallback.", exc)
        return _keyword_fallback(headlines)

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
        }
        for sig in signals
    ]
