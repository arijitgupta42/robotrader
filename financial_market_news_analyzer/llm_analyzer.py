"""
llm_analyzer.py — OpenRouter API client.

Architecture
------------
Calls OpenRouter's /v1/chat/completions endpoint.
Model priority fallback across OPENROUTER_MODELS with retry logic.

All output structure is defined in the system prompt as a plain JSON
schema description.  The response is decoded with json.loads(); if that
fails the raw model output is printed and an empty result is returned.

To add or swap models at runtime without redeploying the Lambda, update
the SSM parameter /sector-scout/openrouter-models (comma-separated list).
Falls back to OPENROUTER_MODELS in config.py if SSM is unreachable.

Environment variable required
------------------------------
    OPENROUTER_API_KEY   — your OpenRouter API key
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Dict, List, Optional

import requests

from config import (
    DISRUPTION_CATEGORIES,
    LSE_SECTORS,
    OPENROUTER_RETRY_DELAYS,
    SCHEDULER_CFG,
    load_openrouter_models,
)
from news_fetcher import Headline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SECTOR_NAMES     = list(LSE_SECTORS.keys())
_DISRUPTION_NAMES = list(DISRUPTION_CATEGORIES.keys())

# Compact representations used in the prompt
_SECTOR_LIST      = " | ".join(_SECTOR_NAMES)
_DISRUPTION_LIST  = " | ".join(_DISRUPTION_NAMES)

# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------
# conviction_drivers: 3 items max, ≤15 words each (tighter than before).
# source_diversity: computed pre-LLM and injected into the user message;
#   the model uses it as a confidence anchor, not a field it must fill.
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
   "conviction_drivers": ["<≤15w fact + [Source]>", "<≤15w fact + [Source]>", "<≤15w fact + [Source]>"]
  }
 ]
}"""

_SYSTEM_PROMPT = f"""\
You are a senior LSE equity portfolio manager.

## ROLE
Identify LSE sub-sectors likely to show POSITIVE price movement over 2-6 WEEKS \
from structural disruptions — not daily noise.

## STEP 1 — MACRO SUMMARY
Summarise in one sentence: BoE stance (cutting/pausing/hiking), UK growth \
surprise direction, and the single dominant global factor \
(rates/China/commodities/geopolitics).

## STEP 2 — SIGNAL SCORING
Target 3-6 signals. Confidence anchors:
  0.85+ structural certainty (multi-month)
  0.70  clear thesis, confirming proof expected 2-4w
  0.55  directional lean, ~35% bear case
  0.35-0.50  marginal but worth surfacing

SCORE when you see:
  • Policy/regulatory change with multi-week implementation lag
  • Commodity or FX move not yet reflected in equities
  • Cluster of earnings beats or misses across a sector
  • Geopolitical shift changing defence budgets or trade routes
  • Technology adoption inflection point

DO NOT score:
  • Single isolated earnings event
  • Already-priced moves (stock already moved >5% on the news)
  • Analyst note with no new fundamental data

Even in stagflation or rate-pause regimes you MUST return ≥2 signals. \
Fallback examples: Aerospace & Defence (rearmament) | Integrated Oil & Gas \
(energy volatility) | Housebuilders (rate expectations). Score at 0.40-0.55 \
and let the downstream filter decide. An empty signals array is only valid if \
headlines contain zero financial content.

## STEP 3 — SOURCE DIVERSITY ADJUSTMENT
Each headline batch includes a per-sector source_diversity score (0-1) \
representing how many distinct outlets back the thesis. \
  diversity ≥ 0.7 → you may score up to your natural conviction ceiling
  diversity 0.4-0.69 → cap confidence at 0.75 (single-outlet cluster risk)
  diversity < 0.4  → cap confidence at 0.60 (treat as thin coverage)

## REFERENCE LISTS
SECTORS (exact spelling required):
{_SECTOR_LIST}

DISRUPTIONS (exact spelling required):
{_DISRUPTION_LIST}

## OUTPUT FORMAT
Respond with ONLY a valid JSON object — no markdown fences, no preamble, \
no trailing commentary. Every conviction_driver must be ≤15 words and end \
with [Source name].
Shape:
{_OUTPUT_SCHEMA}
"""


def _compute_source_diversity(headlines: List[Headline]) -> Dict[str, float]:
    """
    For each LSE sector, compute a source diversity score (0-1) based on
    how many distinct outlet names cover it relative to the maximum seen
    across all sectors.

    A score of 1.0 means this sector is backed by the most outlet-diverse
    set of headlines in this batch.  Scores below 0.4 indicate the sector
    is covered by only one or two outlets (single-outlet cluster risk).

    The score is injected into the user message as a pre-computed anchor
    so the LLM can apply the diversity-cap rule from the system prompt
    without having to count sources itself.
    """
    sector_sources: Dict[str, set] = {}
    for h in headlines:
        text = (h.title + " " + h.summary).lower()
        for sector, keywords in LSE_SECTORS.items():
            if any(kw.lower() in text for kw in keywords):
                sector_sources.setdefault(sector, set()).add(h.source)

    if not sector_sources:
        return {}

    max_count = max(len(v) for v in sector_sources.values())
    if max_count == 0:
        return {}

    return {
        sector: round(len(sources) / max_count, 3)
        for sector, sources in sector_sources.items()
    }


def _build_messages(headlines: List[Headline]) -> list:
    diversity = _compute_source_diversity(headlines)

    # Compact diversity table — only sectors with at least one hit
    if diversity:
        div_lines = "  ".join(
            f"{sector}: {score:.2f}"
            for sector, score in sorted(diversity.items(), key=lambda x: -x[1])
        )
        diversity_block = f"\nSOURCE DIVERSITY SCORES (sector: 0-1):\n{div_lines}\n"
    else:
        diversity_block = ""

    lines = [f"{i+1}. {h.to_text()}" for i, h in enumerate(headlines)]
    user_text = (
        f"Analyse these {len(headlines)} headlines and emit the JSON object."
        + diversity_block
        + "\n\n"
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


_cached_api_key: Optional[str] = None

def _load_api_key() -> str:
    """
    Load the OpenRouter API key from SSM Parameter Store.
    Cached after first fetch for the Lambda container lifetime.
    Falls back to OPENROUTER_API_KEY env var for local development.
    Raises EnvironmentError if neither source yields a key.
    """
    global _cached_api_key

    if _cached_api_key:
        return _cached_api_key

    try:
        import boto3
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
        logger.info("SSM: fetching OpenRouter API key ...")
        resp = ssm.get_parameter(
            Name="/sector-scout/openrouter-api-key",
            WithDecryption=True,
        )
        key = resp["Parameter"]["Value"].strip()
        if key:
            logger.info("SSM: OpenRouter API key loaded successfully.")
            _cached_api_key = key
            return _cached_api_key
        else:
            logger.warning("SSM: parameter exists but value is empty")
    except Exception as exc:
        logger.warning(
            "SSM: could not load OpenRouter API key (%s: %s) — trying env var fallback.",
            type(exc).__name__, exc,
        )

    raise EnvironmentError(
        "OpenRouter API key not found. Tried:\n"
        "  - SSM parameter: /sector-scout/openrouter-api-key (SecureString)\n"
        "Set this before running."
    )
def _get_headers() -> dict:
    api_key = _load_api_key()
    if not api_key:
        raise EnvironmentError(
            "OPENROUTER_API_KEY environment variable is not set. "
            "Create a free account at https://openrouter.ai, generate an API key, "
            "and set it in SSM before running"
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
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=180,
    )
    resp.raise_for_status()
    data   = resp.json()
    choice = data["choices"][0]
    # OpenRouter echoes back which model actually served the request
    model_used = data.get("model", model_chunk[0])
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
    Iterate through models (loaded from SSM at runtime, falling back to
    config.py) in windows of 3, sending each window as a single OpenRouter
    request with `models` + `route: "fallback"`.
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
    models = load_openrouter_models()
    chunks = [models[i:i+3] for i in range(0, len(models), 3)]
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

                decoded = _extract_and_decode(raw, finish_reason)
                if decoded is not None:
                    return decoded, model_used

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


def _keyword_fallback(headlines: List[Headline], diversity: Optional[Dict[str, float]] = None) -> List[Dict]:
    logger.warning("Using keyword heuristic fallback.")
    diversity = diversity or {}
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
            "source_diversity":      round(diversity.get(sector, 0.0), 3),
        })
    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results[:5]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyse_headlines(headlines: List[Headline]) -> tuple[List[Dict], bool]:
    """
    Call OpenRouter with the supplied headlines and return a tuple of:
        (signals, llm_succeeded)

    llm_succeeded = True  — at least one OpenRouter model was called and
                            returned a decodable JSON response.
    llm_succeeded = False — all OpenRouter models failed; signals were
                            produced by the keyword heuristic fallback only.

    Callers that care about pipeline quality (e.g. the Lambda handler) must
    treat llm_succeeded=False as a failure and trigger a retry, even when
    the signals list is non-empty.  Keyword-fallback signals are not
    suitable for live trading decisions.
    """
    if not headlines:
        logger.warning("analyse_headlines called with empty list.")
        return [], False

    # Pre-compute diversity so it's available for both the LLM path and
    # the keyword fallback (used to annotate fallback signals).
    diversity = _compute_source_diversity(headlines)

    messages = _build_messages(headlines)
    logger.info("Invoking OpenRouter on %d headlines ...", len(headlines))

    decoded, model_used = _invoke_with_fallback(messages)

    if decoded is None:
        logger.error("All OpenRouter models failed — using keyword fallback.")
        return _keyword_fallback(headlines, diversity), False

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
        return _keyword_fallback(headlines, diversity), False

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
            "Model returned 0 signals — likely prompt self-censorship. "
            "Check CloudWatch logs for the raw model output."
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
            "source_diversity":      round(diversity.get(validated["sector"], 0.0), 3),
        })

    signals.sort(key=lambda s: s["confidence"], reverse=True)
    logger.info("Returning %d validated signals (llm_succeeded=True).", len(signals))
    return signals, True
