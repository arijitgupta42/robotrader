"""
reddit_analyzer.py — LLM pass over Reddit posts.

Maps bullish retail sentiment in finance/trading subreddits to LSE sectors,
producing RedditSentimentSignal objects that are consumed by consolidator.py.

Architecture mirrors llm_analyzer.py: OpenRouter call with model fallback,
JSON schema extraction, light validation.  The same model pool is used.

Why a separate LLM pass (rather than mixing Reddit posts into the news prompt)?
  - Reddit posts and news headlines have very different signal-to-noise profiles.
    A dedicated pass lets us tune the prompt to filter memes/noise and extract
    genuine conviction signals (DD posts, position disclosures, earnings plays).
  - The consolidator then gets two independently scored inputs, which makes
    its job (agreement/disagreement detection) cleaner.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Dict, List, Optional

import requests

from config import (
    DISRUPTION_CATEGORIES,
    LSE_SECTORS,
    OPENROUTER_RETRY_DELAYS,
    load_openrouter_models,
)
from reddit_fetcher import RedditPost
from llm_analyzer import _load_api_key, _extract_and_decode   # reuse helpers

logger = logging.getLogger(__name__)

_SECTOR_NAMES     = list(LSE_SECTORS.keys())
_DISRUPTION_NAMES = list(DISRUPTION_CATEGORIES.keys())
_SECTOR_LIST      = " | ".join(_SECTOR_NAMES)

# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

_REDDIT_SCHEMA = """\
{"reddit_signals": [
  {"sector": "<exact from SECTORS>",
   "bullish_conviction": 0.0,
   "retail_thesis": "<≤30w: what the crowd believes>",
   "representative_tickers": ["<LSE ticker or name>"],
   "signal_quality": "<DD|Discussion|Meme|News|Mixed>",
   "crowd_risk": "<≤20w: main way this crowd thesis is wrong>",
   "post_count": 0,
   "avg_score": 0.0
  }
 ]
}"""

_SYSTEM_PROMPT = f"""\
You are an LSE quantitative analyst specialising in retail sentiment.

Your job: read a batch of Reddit posts from finance/trading communities and
identify LSE-relevant sectors where retail traders are BULLISH right now.

CRITICAL FILTERS — only surface a sector if:
  1. Multiple posts (or a high-conviction single DD post with significant engagement)
     express directional bullish views, not just curiosity or questions.
  2. The thesis relates to a company or sector listed (or dual-listed) on the
     London Stock Exchange, or to a macro/commodity factor that directly moves
     LSE-listed equities (e.g. oil price → BP/Shell, copper → mining stocks).
  3. The post is not pure meme content, loss porn, or off-topic discussion.

QUALITY GRADES:
  DD       — original research post, detailed fundamentals/catalyst
  Discussion — structured debate with real investment thesis
  News     — sharing/reacting to a breaking story with clear LSE impact
  Mixed    — combination of the above
  Meme     — humour/meme driven, minimal investment thesis

Score bullish_conviction 0-1:
  0.80+  Strong DD + high engagement + clear LSE linkage
  0.60   Multiple quality Discussion posts in agreement
  0.40   Some signal, noisy or indirect LSE linkage
  0.20   Weak or single anecdote

If no posts contain genuine LSE-relevant bullish sentiment, return an empty
reddit_signals array — do NOT fabricate signals.

SECTORS (use exact spelling):
{_SECTOR_LIST}

OUTPUT: respond with ONLY a valid JSON object — no fences, no preamble.
Shape:
{_REDDIT_SCHEMA}
"""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_reddit_messages(posts: List[RedditPost]) -> list:
    lines = [f"{i+1}. {p.to_text()}" for i, p in enumerate(posts)]
    user_text = (
        f"Analyse these {len(posts)} Reddit posts and emit the JSON object.\n\n"
        + "\n\n".join(lines)
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": user_text},
    ]


# ---------------------------------------------------------------------------
# OpenRouter call (mirrors llm_analyzer._invoke_with_fallback)
# ---------------------------------------------------------------------------

_OR_URL = "https://openrouter.ai/api/v1/chat/completions"


def _get_headers() -> dict:
    return {
        "Authorization": f"Bearer {_load_api_key()}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://github.com/your-org/sector-scout",
        "X-Title":       "LSE Sector Scout - Reddit Analyzer",
    }


def _call_openrouter(messages: list, model_chunk: list) -> tuple:
    payload = {
        "model":       model_chunk[0],
        "messages":    messages,
        "temperature": 0.25,          # lower temp — we want consistent extraction
        "max_tokens":  2048,
        "reasoning":   {"effort": "high", "exclude": True},
        "response_format": {"type": "json_object"},
        "models": model_chunk,
        "route":  "fallback",
    }
    resp = requests.post(_OR_URL, headers=_get_headers(), data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), timeout=120)
    resp.raise_for_status()
    data   = resp.json()
    choice = data["choices"][0]
    model_used = data.get("model", model_chunk[0])
    return choice["message"]["content"], choice.get("finish_reason", "unknown"), model_used


def _invoke_with_fallback(messages: list) -> tuple[Optional[dict], Optional[str]]:
    models = load_openrouter_models()
    chunks = [models[i:i+3] for i in range(0, len(models), 3)]

    for chunk_idx, chunk in enumerate(chunks, start=1):
        logger.info(
            "Reddit LLM: chunk %d/%d — models: %s",
            chunk_idx, len(chunks), " → ".join(chunk),
        )
        for attempt, delay in enumerate(OPENROUTER_RETRY_DELAYS, start=1):
            try:
                raw, finish_reason, model_used = _call_openrouter(messages, chunk)
                decoded = _extract_and_decode(raw, finish_reason)
                if decoded is not None:
                    logger.info("Reddit LLM: served by %s (chunk %d)", model_used, chunk_idx)
                    return decoded, model_used
                logger.error("Reddit LLM: JSON decode failed on chunk %d.", chunk_idx)
                break

            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status in (429, 502, 503, 504):
                    logger.warning("Reddit LLM chunk %d: HTTP %d — sleeping %ds.", chunk_idx, status, delay)
                    time.sleep(delay)
                else:
                    logger.error("Reddit LLM chunk %d: HTTP %d non-retryable.", chunk_idx, status)
                    break

            except requests.Timeout:
                logger.warning("Reddit LLM chunk %d: timeout (attempt %d).", chunk_idx, attempt)
                time.sleep(delay)

            except Exception as exc:
                logger.error("Reddit LLM chunk %d: unexpected error: %s.", chunk_idx, exc)
                break

        else:
            logger.warning("Reddit LLM chunk %d: all retries exhausted.", chunk_idx)

    logger.error("Reddit LLM: all chunks exhausted.")
    return None, None


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field


@dataclass
class RedditSentimentSignal:
    sector:                  str
    bullish_conviction:      float   # 0-1
    retail_thesis:           str
    representative_tickers:  List[str]
    signal_quality:          str     # DD | Discussion | Meme | News | Mixed
    crowd_risk:              str
    post_count:              int
    avg_score:               float
    source_posts:            List[RedditPost] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sector":                 self.sector,
            "bullish_conviction":     self.bullish_conviction,
            "retail_thesis":          self.retail_thesis,
            "representative_tickers": self.representative_tickers,
            "signal_quality":         self.signal_quality,
            "crowd_risk":             self.crowd_risk,
            "post_count":             self.post_count,
            "avg_score":              self.avg_score,
            "source_post_count":      len(self.source_posts),
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_reddit_signal(raw: dict, posts: List[RedditPost]) -> Optional[RedditSentimentSignal]:
    sector = raw.get("sector", "")
    if sector not in _SECTOR_NAMES:
        match = next((s for s in _SECTOR_NAMES if s.lower() == sector.lower()), None)
        if match:
            sector = match
        else:
            logger.warning("Reddit signal: unknown sector %r — dropping.", sector)
            return None

    conviction = raw.get("bullish_conviction", 0.0)
    if not isinstance(conviction, (int, float)):
        return None
    conviction = max(0.0, min(1.0, float(conviction)))

    # Attach source posts that mention this sector's keywords
    kws = [kw.lower() for kw in LSE_SECTORS.get(sector, [])]
    relevant_posts = [
        p for p in posts
        if any(kw in p.title.lower() or kw in p.selftext.lower() for kw in kws)
    ] or []

    return RedditSentimentSignal(
        sector                 = sector,
        bullish_conviction     = round(conviction, 4),
        retail_thesis          = raw.get("retail_thesis", "")[:300],
        representative_tickers = raw.get("representative_tickers", [])[:5],
        signal_quality         = raw.get("signal_quality", "Mixed"),
        crowd_risk             = raw.get("crowd_risk", "")[:200],
        post_count             = max(0, int(raw.get("post_count", 0))),
        avg_score              = float(raw.get("avg_score", 0.0)),
        source_posts           = relevant_posts,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyse_reddit_posts(posts: List[RedditPost]) -> tuple[List[RedditSentimentSignal], bool]:
    """
    Run LLM analysis over Reddit posts.

    Returns (signals, llm_succeeded).
    llm_succeeded=False means the LLM failed; signals list will be empty.
    Unlike the news pipeline, there is no keyword fallback here — Reddit
    heuristics would be too noisy to be useful.
    """
    if not posts:
        logger.warning("analyse_reddit_posts called with empty list.")
        return [], False

    # Cap to avoid context window issues — prioritise highest-score posts
    capped_posts = sorted(posts, key=lambda p: p.score, reverse=True)[:60]
    logger.info("Reddit LLM: analysing %d posts (from %d total) ...", len(capped_posts), len(posts))

    messages = _build_reddit_messages(capped_posts)
    decoded, model_used = _invoke_with_fallback(messages)

    if decoded is None:
        logger.error("Reddit LLM: all models failed — no Reddit sentiment signals.")
        return [], False

    raw_signals = decoded.get("reddit_signals", [])
    if not isinstance(raw_signals, list):
        logger.warning("Reddit LLM: 'reddit_signals' is not a list.")
        return [], False

    logger.info("Reddit LLM: %d raw signals from %s.", len(raw_signals), model_used)

    signals: List[RedditSentimentSignal] = []
    for raw in raw_signals:
        sig = _validate_reddit_signal(raw, capped_posts)
        if sig is not None:
            signals.append(sig)

    signals.sort(key=lambda s: s.bullish_conviction, reverse=True)
    logger.info("Reddit LLM: %d validated sentiment signals.", len(signals))
    return signals, True
