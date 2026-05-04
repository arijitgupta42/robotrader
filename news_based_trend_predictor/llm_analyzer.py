"""
llm_analyzer.py — OpenRouter API client.

Architecture
------------
Calls OpenRouter's /v1/chat/completions endpoint.
Model priority fallback across OPENROUTER_MODELS with retry logic.

All output structure is defined in the system prompt as a plain JSON
schema description.  The response is decoded with json.loads(); if that
fails the raw model output is printed and an empty result is returned.

To add or swap a model: edit OPENROUTER_MODELS in config.py only.

Environment variable required
------------------------------
    OPENROUTER_API_KEY   — your OpenRouter API key

Raw model responses
-------------------
Every raw response string from the model is written to:
    ./raw_model_responses/raw_<timestamp>_<model_slug>.txt
This allows post-hoc inspection when JSON decoding fails or signals are
unexpectedly empty.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests

from config import (
    DISRUPTION_CATEGORIES,
    LSE_SECTORS,
    OPENROUTER_MODELS,
    OPENROUTER_PRIMARY,
    OPENROUTER_RETRY_DELAYS,
    SCHEDULER_CFG,
)
from news_fetcher import Headline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Raw response persistence
# ---------------------------------------------------------------------------

_RAW_RESPONSE_DIR = Path("raw_model_responses")


def _save_raw_response(raw: str, model: str, suffix: str = "") -> Path:
    """
    Write the raw model response string to a timestamped file under
    ./raw_model_responses/.  Returns the path written.

    suffix  — optional tag appended to the filename, e.g. "decode_failed"
    """
    _RAW_RESPONSE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", model)[:60]
    tag = f"_{suffix}" if suffix else ""
    path = _RAW_RESPONSE_DIR / f"raw_{ts}_{slug}{tag}.txt"
    path.write_text(raw, encoding="utf-8")
    logger.info("Raw model response saved → %s", path)
    return path


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SECTOR_NAMES     = list(LSE_SECTORS.keys())
_DISRUPTION_NAMES = list(DISRUPTION_CATEGORIES.keys())

# Compact representations used in the prompt
_SECTOR_LIST      = " | ".join(_SECTOR_NAMES)
_DISRUPTION_LIST  = " | ".join(_DISRUPTION_NAMES)

# ---------------------------------------------------------------------------
# Output schema — terse field comments keep the response compact.
# conviction_drivers is capped at 2 items (source tag mandatory) to bound
# the single largest per-signal token cost.
# ---------------------------------------------------------------------------
_OUTPUT_SCHEMA = """\
{"macro":
  {"regime": "<1 sentence: BoE stance + dominant global factor>"},
 "signals": [
  {"sector": "<exact from SECTORS>",
   "confidence": 0.0,
   "disruption_type": "<exact from DISRUPTIONS>",
   "disruption_strength": 0.0,
   "time_to_impact_weeks": 0,
   "bear_case_probability": 0.0,
   "propagation": "<≤25w: mechanism>",
   "invalidation_risk": "<≤20w: kill-switch + (prob%)>",
   "rationale": "<≤25w: why now>",
   "key_catalysts": ["<near-term confirming event>"],
   "correlated_sectors": ["<0-2 exact sector names>"],
   "conviction_drivers": ["<headline paraphrase [Source]>", "<headline paraphrase [Source]>"]
  }
 ]
}"""

_SYSTEM_PROMPT = f"""\
You are a senior LSE equity portfolio manager. Identify LSE sub-sectors likely \
to show POSITIVE price movement over 2-6 WEEKS from structural disruptions — \
not daily noise.

MACRO FIRST: Summarise BoE stance (cutting/pausing/hiking), UK growth surprise \
direction, and the single dominant global factor (rates/China/commodities/geopolitics).

SCORE SIGNALS — target 3-6. Confidence anchors:
  0.85+ structural certainty (multi-month)  |  0.70 clear, proof 2-4w away
  0.55 directional lean, ~35% bear case     |  0.35-0.50 marginal but surfaceable

SCORE if: policy/regulatory with multi-week implementation lag | commodity/FX \
move not yet in equities | cluster of earnings beats/misses | geopolitical \
shift changing defence budgets or trade routes | tech adoption inflection.

DO NOT score: single isolated earnings event | already-priced price moves | \
analyst note without new fundamentals.

Even in stagflation/rate-pause regimes you MUST return ≥2 signals. Examples: \
Aerospace & Defence (rearmament) | Integrated Oil & Gas (energy volatility) | \
Housebuilders (rate expectations). Score at 0.40-0.55 and let the downstream \
filter decide. An empty signals array is only valid if headlines contain zero \
financial content.

SECTORS (use exact spelling):
{_SECTOR_LIST}

DISRUPTIONS (use exact spelling):
{_DISRUPTION_LIST}

OUTPUT: respond with ONLY a valid JSON object — no fences, no preamble.
Shape:
{_OUTPUT_SCHEMA}
"""


def _build_messages(headlines: List[Headline]) -> list:
    lines = [f"{i+1}. {h.to_text()}" for i, h in enumerate(headlines)]
    user_text = (
        f"Analyse these {len(headlines)} headlines and emit the JSON object.\n\n"
        + "\n".join(lines)
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


def _build_payload(messages: list, model_chunk: list) -> dict:
    """
    Build the request payload for OpenRouter's /v1/chat/completions endpoint.

    OpenRouter's native fallback accepts up to 3 models per request via
    `models` + `route: "fallback"`.  The first entry in the chunk is also
    set as the top-level `model` field (required by the API).  If all models
    in the chunk are exhausted server-side, the caller tries the next chunk.
    """
    return {
        "model":       model_chunk[0],
        "messages":    messages,
        "temperature": 0.35,
        "max_tokens":  4096,
        "reasoning":   {"effort": "high", "exclude": True},
        "response_format": {"type": "json_object"},
        "models": model_chunk,   # OpenRouter native fallback — max 3 per request
        "route":  "fallback",
    }


def _call_openrouter(messages: list, model_chunk: list) -> tuple:
    """Returns (content, finish_reason, model_used). finish_reason is 'stop' on clean completion."""
    payload = _build_payload(messages, model_chunk)
    resp = requests.post(
        _OR_URL,
        headers=_get_headers(),
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    data   = resp.json()
    choice = data["choices"][0]
    # OpenRouter echoes back which model actually served the request
    model_used = data.get("model", OPENROUTER_PRIMARY)
    return choice["message"]["content"], choice.get("finish_reason", "unknown"), model_used


# ---------------------------------------------------------------------------
# JSON extraction + naive decode
# ---------------------------------------------------------------------------

def _extract_and_decode(raw: str, finish_reason: str = "stop") -> Optional[dict]:
    """
    Attempt to decode a JSON object from the raw model response.

    Strategy:
      1. Warn immediately if finish_reason indicates truncation.
      2. Strip markdown fences and <think> blocks.
      3. Try json.loads on the cleaned string.
      4. If that fails, scan for the outermost {...} and try again.
      5. If that also fails, print the raw response and return None.
    """
    if finish_reason == "length":
        logger.warning(
            "Model hit max_tokens limit — response truncated (finish_reason=length). "
            "The JSON will be incomplete. Consider raising max_tokens further."
        )

    # Strip markdown code fences (applied once, on raw → clean)
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

    # Strip <think>...</think> blocks leaked by reasoning models
    clean = re.sub(r"<think>[\s\S]*?</think>", "", clean, flags=re.IGNORECASE).strip()

    # Attempt 1: direct parse
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # Attempt 2: extract outermost { ... }
    match = re.search(r"\{[\s\S]+\}", clean)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # All attempts failed — print the raw response so the caller can inspect it
    print("\n" + "=" * 72)
    print("  JSON DECODE FAILED — raw model response:")
    print("=" * 72)
    print(raw)
    print("=" * 72 + "\n")
    return None


# ---------------------------------------------------------------------------
# Light validation of decoded dict (no Pydantic)
# ---------------------------------------------------------------------------

def _validate_signal(raw: dict) -> Optional[dict]:
    """
    Check the mandatory fields exist and have sensible types.
    Drops individual invalid signals rather than crashing the whole run.
    Coerces minor issues (unknown sector/disruption) with a warning.
    """
    sector = raw.get("sector", "")
    if sector not in _SECTOR_NAMES:
        # Case-insensitive rescue
        match = next((s for s in _SECTOR_NAMES if s.lower() == sector.lower()), None)
        if match:
            raw["sector"] = match
        else:
            logger.warning(
                "Dropping signal: unknown sector %r. Full signal: %s",
                sector, json.dumps(raw, default=str)[:400],
            )
            return None

    disruption = raw.get("disruption_type", "")
    if disruption not in _DISRUPTION_NAMES:
        match = next((d for d in _DISRUPTION_NAMES if d.lower() == disruption.lower()), None)
        if match:
            raw["disruption_type"] = match
        else:
            logger.warning(
                "Signal for %r has unknown disruption_type %r — keeping with warning.",
                sector, disruption,
            )

    # Numeric range clamps — verbose on failure so dropped signals are visible
    for key in ("confidence", "disruption_strength", "bear_case_probability"):
        val = raw.get(key)
        if not isinstance(val, (int, float)):
            logger.warning(
                "Signal for %r dropping — field %r has non-numeric value %r. "
                "Full signal: %s",
                sector, key, val, json.dumps(raw, default=str)[:400],
            )
            return None
        raw[key] = max(0.0, min(1.0, float(val)))

    time_w = raw.get("time_to_impact_weeks")
    if not isinstance(time_w, int):
        try:
            raw["time_to_impact_weeks"] = max(1, min(12, int(time_w)))
        except (TypeError, ValueError):
            raw["time_to_impact_weeks"] = 3

    # Ensure list fields are lists
    for key in ("key_catalysts", "correlated_sectors", "conviction_drivers"):
        if not isinstance(raw.get(key), list):
            raw[key] = []

    # Trim correlated_sectors to valid names only
    raw["correlated_sectors"] = [
        s for s in raw.get("correlated_sectors", []) if s in _SECTOR_NAMES
    ][:2]

    return raw


# ---------------------------------------------------------------------------
# Retry + model fallback logic
# ---------------------------------------------------------------------------

def _invoke_with_fallback(messages: list) -> tuple[Optional[dict], Optional[str]]:
    """
    Iterate through OPENROUTER_MODELS in windows of 3, sending each window
    as a single OpenRouter request with `models` + `route: "fallback"`.
    OpenRouter handles intra-chunk fallback server-side; this function handles
    inter-chunk fallback (i.e. all 3 models in a window failed → try next window).

    Within each chunk, transient errors (429, 5xx, timeout) are retried up to
    len(OPENROUTER_RETRY_DELAYS) times before the chunk is abandoned and the
    next chunk is tried.  Permanent 4xx errors abort immediately.

    Example with 6 models:
      Chunk 1: [model_0, model_1, model_2]  — one OpenRouter request
      Chunk 2: [model_3, model_4, model_5]  — tried only if chunk 1 fully fails

    Returns (decoded_dict, model_name_that_served_request) on success,
    or (None, None) if every chunk is exhausted.
    """
    chunks = [OPENROUTER_MODELS[i:i+3] for i in range(0, len(OPENROUTER_MODELS), 3)]
    total_chunks = len(chunks)

    for chunk_idx, chunk in enumerate(chunks, start=1):
        logger.info(
            "OpenRouter: chunk %d/%d — models: %s",
            chunk_idx, total_chunks, " → ".join(chunk),
        )

        for attempt, delay in enumerate(OPENROUTER_RETRY_DELAYS, start=1):
            try:
                raw, finish_reason, model_used = _call_openrouter(messages, chunk)

                logger.info("+ Served by model: %s (chunk %d/%d)", model_used, chunk_idx, total_chunks)
                _save_raw_response(raw, model_used)

                decoded = _extract_and_decode(raw, finish_reason)
                if decoded is not None:
                    return decoded, model_used

                _save_raw_response(raw, model_used, suffix="decode_failed")
                logger.error(
                    "Could not decode JSON from chunk %d — trying next chunk.", chunk_idx
                )
                break  # JSON decode failed — skip to next chunk immediately

            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0

                if status == 429:
                    logger.warning(
                        "Chunk %d: rate limited (429) — sleeping %ds (attempt %d/%d).",
                        chunk_idx, delay, attempt, len(OPENROUTER_RETRY_DELAYS),
                    )
                    time.sleep(delay)

                elif status in (502, 503, 504):
                    logger.warning(
                        "Chunk %d: gateway error (%d) — sleeping %ds (attempt %d/%d).",
                        chunk_idx, status, delay, attempt, len(OPENROUTER_RETRY_DELAYS),
                    )
                    time.sleep(delay)

                else:
                    logger.error(
                        "Chunk %d: HTTP %d — non-retryable, trying next chunk.\n%s",
                        chunk_idx, status, (exc.response.text or "")[:400],
                    )
                    break  # non-retryable — move to next chunk

            except requests.Timeout:
                logger.warning(
                    "Chunk %d: timeout (attempt %d/%d) — sleeping %ds.",
                    chunk_idx, attempt, len(OPENROUTER_RETRY_DELAYS), delay,
                )
                time.sleep(delay)

            except Exception as exc:
                logger.error("Chunk %d: unexpected error: %s — trying next chunk.", chunk_idx, exc)
                break

        else:
            logger.warning("Chunk %d: all retry attempts exhausted — trying next chunk.", chunk_idx)

    logger.error("All %d chunks exhausted — no model succeeded.", total_chunks)
    return None, None


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
        confidence = min(
            0.40 + (kw_hits / 12) * 0.35 + (dis_hits / 15) * 0.15 + (pos_hits / 20) * 0.10,
            0.85,
        )
        results.append({
            "sector":                sector,
            "confidence":            round(confidence, 3),
            "disruption_type":       "Regulatory / Policy Shift",
            "disruption_strength":   round(min(dis_hits / 10, 1.0), 3),
            "time_to_impact_weeks":  3,
            "propagation":           f"Keyword signal: {kw_hits} sector matches (heuristic fallback).",
            "invalidation_risk":     "Heuristic — validate manually.",
            "rationale":             f"Keyword-based: {kw_hits} sector hits in headlines.",
            "bear_case_probability": round(1.0 - confidence, 3),
            "key_catalysts":         ["Manual validation required"],
            "correlated_sectors":    [],
            "conviction_drivers":    ["Heuristic fallback — no LLM analysis available"],
            "macro_regime_summary":  "Heuristic fallback — no LLM macro analysis available.",
        })
    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results[:5]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyse_headlines(headlines: List[Headline]) -> List[Dict]:
    """
    Call OpenRouter with the supplied headlines and return a list of
    swing signal dicts.

    Falls back to keyword heuristic if all OpenRouter models fail.
    """
    if not headlines:
        logger.warning("analyse_headlines called with empty list.")
        return []

    messages = _build_messages(headlines)
    logger.info("Invoking OpenRouter on %d headlines ...", len(headlines))

    decoded, model_used = _invoke_with_fallback(messages)

    if decoded is None:
        logger.error("All OpenRouter models failed — using keyword fallback.")
        return _keyword_fallback(headlines)

    macro_obj = decoded.get("macro", {})
    macro = (
        macro_obj.get("regime", "")
        if isinstance(macro_obj, dict)
        else decoded.get("macro_regime_summary", "")  # graceful fallback for old schema
    )
    if macro:
        logger.info("Macro regime: %s", macro)

    raw_signals = decoded.get("signals", [])
    if not isinstance(raw_signals, list):
        logger.warning("'signals' field is not a list — using keyword fallback.")
        return _keyword_fallback(headlines)

    logger.info(
        "Raw signals from model (%s): %d total before validation.",
        model_used, len(raw_signals),
    )
    for i, raw in enumerate(raw_signals):
        logger.info(
            "  Signal %d: sector=%r  conf=%s  disruption=%r",
            i + 1,
            raw.get("sector"),
            raw.get("confidence"),
            raw.get("disruption_type"),
        )

    if len(raw_signals) == 0:
        logger.warning(
            "Model returned 0 signals. Raw response saved to %s/. "
            "This is likely prompt self-censorship — review the saved file.",
            _RAW_RESPONSE_DIR,
        )

    signals = []
    for raw in raw_signals:
        validated = _validate_signal(raw)
        if validated is None:
            continue
        signals.append({
            "sector":                validated["sector"],
            "confidence":            round(validated["confidence"], 4),
            "disruption_type":       validated["disruption_type"],
            "disruption_strength":   round(validated["disruption_strength"], 4),
            "time_to_impact_weeks":  validated["time_to_impact_weeks"],
            "propagation":           validated.get("propagation", ""),
            "invalidation_risk":     validated.get("invalidation_risk", ""),
            "rationale":             validated.get("rationale", ""),
            "bear_case_probability": round(validated["bear_case_probability"], 4),
            "key_catalysts":         validated.get("key_catalysts", []),
            "correlated_sectors":    validated.get("correlated_sectors", []),
            "conviction_drivers":    validated.get("conviction_drivers", []),
            "macro_regime_summary":  macro,
        })

    signals.sort(key=lambda s: s["confidence"], reverse=True)
    logger.info("Returning %d validated signals.", len(signals))
    return signals
