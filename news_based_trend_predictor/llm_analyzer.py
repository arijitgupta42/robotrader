"""
llm_analyzer.py — Loads Gemma 4 E2B-it and performs medium-term swing
analysis over a batch of financial headlines.

API changes from previous version
----------------------------------
Gemma 4 does NOT use the transformers `pipeline()` abstraction.
The correct pattern (from huggingface.co/google/gemma-4-E2B-it) is:

    processor = AutoProcessor.from_pretrained(MODEL_ID)
    model     = AutoModelForCausalLM.from_pretrained(MODEL_ID, ...)

    text   = processor.apply_chat_template(messages,
                 tokenize=False, add_generation_prompt=True,
                 enable_thinking=True)        # ← Gemma 4 native CoT
    inputs = processor(text=text, return_tensors="pt").to(model.device)
    input_len = inputs["input_ids"].shape[-1]
    outputs   = model.generate(**inputs, max_new_tokens=1024, ...)
    decoded   = processor.decode(outputs[0][input_len:],
                    skip_special_tokens=False)  # keep thinking tags

When enable_thinking=True the model wraps its reasoning in:
    <|channel>thought\n[internal reasoning]<channel|>
    [JSON answer]

We strip that block before JSON parsing.

Medium-term swing philosophy
-----------------------------
We are NOT asking "did the sector go up today?".
We are asking: "Is there a structural disruption in the news that will
force the market to reprice an entire LSE sector over the next 2–6 weeks,
in the same way semiconductor stocks moved during the export-control
escalation?"

The model is explicitly instructed to:
  1. Identify the DISRUPTION TYPE (from a taxonomy).
  2. Explain the PROPAGATION MECHANISM — why this flows through to
     LSE-listed equities, not just the headline event.
  3. Estimate a TIME-TO-IMPACT window (weeks).
  4. Assign confidence only after this reasoning is complete.
"""

from __future__ import annotations

import json
import logging
import re
import warnings
from typing import Dict, List, Optional

import torch

from config import DISRUPTION_CATEGORIES, LSE_SECTORS, MODEL_CFG, SCHEDULER_CFG
from news_fetcher import Headline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

_processor = None
_model     = None


def _load_model() -> None:
    global _processor, _model
    if _model is not None:
        return

    from transformers import (
        AutoProcessor,
        AutoModelForCausalLM,
        BitsAndBytesConfig,
    )

    # --- GPU diagnostic (shown once at startup) ---
    logger.info("PyTorch version  : %s", torch.__version__)
    logger.info("CUDA available   : %s", torch.cuda.is_available())
    if torch.cuda.is_available():
        logger.info("CUDA version     : %s", torch.version.cuda)
        for i in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(i)
            logger.info(
                "GPU %d            : %s  (%.1f GB VRAM)",
                i, props.name, props.total_memory / 1024**3,
            )
    elif torch.backends.mps.is_available():
        logger.info("Apple MPS        : available")
    else:
        logger.warning(
            "No GPU detected by PyTorch. "
            "If you have a GPU, your PyTorch install is likely CPU-only. "
            "Fix: pip install torch --index-url https://download.pytorch.org/whl/cu121"
        )

    logger.info("Loading %s …", MODEL_CFG.model_id)

    dtype_map = {
        "bfloat16": torch.bfloat16,
        "float16":  torch.float16,
        "float32":  torch.float32,
        "auto":     "auto",
    }
    torch_dtype = dtype_map.get(MODEL_CFG.torch_dtype, torch.bfloat16)

    load_kwargs: dict = {
        "device_map":           "auto",
        "attn_implementation":  MODEL_CFG.attn_implementation,
    }

    if MODEL_CFG.use_4bit_quantisation and torch.cuda.is_available():
        bnb_cfg = BitsAndBytesConfig(
            load_in_4bit              = True,
            bnb_4bit_compute_dtype    = torch.bfloat16,
            bnb_4bit_use_double_quant = True,
            bnb_4bit_quant_type       = "nf4",
        )
        load_kwargs["quantization_config"] = bnb_cfg
        logger.info("4-bit NF4 quantisation enabled.")
    else:
        # FIX: was "dtype" (silently ignored), must be "torch_dtype"
        load_kwargs["torch_dtype"] = torch_dtype

    if not torch.cuda.is_available() and not torch.backends.mps.is_available():
        warnings.warn(
            "No GPU detected — running Gemma 4 E2B on CPU (float32). "
            "Inference will be very slow. Consider a GPU or the E2B-GGUF "
            "quantised variant for CPU-only deployments.",
            RuntimeWarning,
        )
        load_kwargs["torch_dtype"] = torch.float32

    _processor = AutoProcessor.from_pretrained(
        MODEL_CFG.model_id,
        padding_side = "left",   # recommended for Gemma 4 batch generation
    )
    _model = AutoModelForCausalLM.from_pretrained(
        MODEL_CFG.model_id,
        **load_kwargs,
    )
    logger.info("Model loaded on device(s): %s", _model.hf_device_map if hasattr(_model, "hf_device_map") else "cpu")


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SECTOR_LIST = "\n".join(f"  - {s}" for s in LSE_SECTORS)

_DISRUPTION_BLOCK = "\n".join(
    f"  [{name}]: {desc}"
    for name, desc in DISRUPTION_CATEGORIES.items()
)

_SYSTEM_PROMPT = f"""\
You are a senior quantitative strategist at a UK hedge fund specialising in
medium-term swing trades on the London Stock Exchange (LSE).

Your task is to read a set of recent financial news headlines and identify
which LSE sectors are most likely to experience significant POSITIVE price
movement over the next 2 to 6 weeks — NOT just today.

You are NOT looking for daily noise. You are looking for structural
disruptions that force a broad market repricing of an entire sector —
the kind of sustained move that happened to semiconductor stocks when
export controls created a dislocation the market took weeks to fully price.

DISRUPTION TYPES to watch for:
{_DISRUPTION_BLOCK}

LSE SECTORS to consider:
{_SECTOR_LIST}

For each sector signal you identify, reason through:
1. WHAT is the disruption, and which disruption type does it belong to?
2. HOW does it propagate to LSE-listed companies in this sector?
   (Not just "prices go up" — which specific companies, revenue lines,
   or cost structures are affected, and why has this NOT been fully priced?)
3. WHEN will the market price this in? Give a 2–6 week window estimate.
4. WHAT is the key risk that could invalidate the thesis?

After completing your reasoning, output ONLY a JSON array (no markdown fences,
no explanation outside the array) with this exact schema per item:

  "sector"              : one of the sector names listed above
  "confidence"          : float 0.0–1.0 (your conviction in positive movement)
  "disruption_type"     : one of the disruption type names listed above
  "disruption_strength" : float 0.0–1.0 (how significant the structural break is)
  "time_to_impact_weeks": integer, estimated weeks before full market pricing (1–8)
  "propagation"         : concise sentence (≤30 words) explaining the mechanism
  "invalidation_risk"   : concise sentence (≤20 words) — what would kill the thesis
  "rationale"           : concise sentence (≤30 words) — overall case summary

Sort by confidence descending. Include only sectors with confidence >= 0.4.
If no sector shows a genuine medium-term disruption thesis, return [].

Do NOT confuse a one-day news event with a structural disruption. A single
earnings beat is NOT a swing signal. Look for systemic shifts.
"""


def _build_user_message(headlines: List[Headline]) -> str:
    lines = [f"{i+1}. {h.to_text()}" for i, h in enumerate(headlines)]
    return "Recent headlines for analysis:\n\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Gemma 4 thinking-block stripper
# ---------------------------------------------------------------------------

_THINK_RE = re.compile(
    r"<\|channel\>thought\n.*?<channel\|>",
    re.DOTALL,
)

def _strip_thinking(text: str) -> str:
    """Remove Gemma 4's chain-of-thought block, leaving only the answer."""
    return _THINK_RE.sub("", text).strip()


# ---------------------------------------------------------------------------
# JSON parsing with fallback
# ---------------------------------------------------------------------------

def _extract_json(raw: str) -> Optional[list]:
    cleaned = _strip_thinking(raw)

    # Try direct parse
    try:
        return json.loads(cleaned.strip())
    except json.JSONDecodeError:
        pass

    # Try to find the first [...] block
    match = re.search(r"\[.*?\]", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning("JSON extraction failed from output: %s", cleaned[:300])
    return None


# ---------------------------------------------------------------------------
# Keyword-scoring fallback (used if LLM output is completely unparseable)
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
    all_text = " ".join(h.title.lower() + " " + h.summary.lower() for h in headlines)

    results = []
    for sector, keywords in LSE_SECTORS.items():
        kw_hits = sum(1 for kw in keywords if kw.lower() in all_text)
        if kw_hits == 0:
            continue
        dis_hits = sum(1 for dw in _DISRUPTION_WORDS if dw in all_text)
        pos_hits = sum(1 for pw in _POSITIVE_WORDS if pw in all_text)
        confidence = min(0.40 + (kw_hits / 12) * 0.35 + (dis_hits / 15) * 0.15 + (pos_hits / 20) * 0.10, 0.85)
        results.append({
            "sector":               sector,
            "confidence":           round(confidence, 3),
            "disruption_type":      "Regulatory / Policy Shift",
            "disruption_strength":  round(min(dis_hits / 10, 1.0), 3),
            "time_to_impact_weeks": 3,
            "propagation":          f"Keyword signal: {kw_hits} sector matches, {dis_hits} disruption signals (heuristic).",
            "invalidation_risk":    "Heuristic fallback — validate manually.",
            "rationale":            f"Keyword-based heuristic: {kw_hits} sector hits in headlines.",
        })

    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results[:5]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyse_headlines(headlines: List[Headline]) -> List[Dict]:
    """
    Run Gemma 4 E2B-it over the supplied headlines and return a list of
    medium-term swing signal dicts:
      [{
          "sector": str,
          "confidence": float,
          "disruption_type": str,
          "disruption_strength": float,
          "time_to_impact_weeks": int,
          "propagation": str,
          "invalidation_risk": str,
          "rationale": str,
      }, ...]

    Falls back to a keyword heuristic if the model output is unparseable.
    """
    if not headlines:
        logger.warning("analyse_headlines called with empty list.")
        return []

    _load_model()

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": _build_user_message(headlines)},
    ]

    logger.info("Applying chat template (thinking=%s) …", MODEL_CFG.enable_thinking)

    # === Correct Gemma 4 E2B API ===
    # Step 1: apply_chat_template → formatted string (not tensors yet)
    text = _processor.apply_chat_template(
        messages,
        tokenize            = False,
        add_generation_prompt = True,
        enable_thinking     = MODEL_CFG.enable_thinking,
    )

    # Step 2: processor call → tensors
    inputs    = _processor(text=text, return_tensors="pt").to(_model.device)
    input_len = inputs["input_ids"].shape[-1]

    logger.info("Running inference on %d headlines (input_len=%d tokens) …",
                len(headlines), input_len)

    try:
        # skip_special_tokens=False so we can detect and strip the <|channel>thought<channel|> block
        with torch.inference_mode():
            output_ids = _model.generate(
                **inputs,
                max_new_tokens = MODEL_CFG.max_new_tokens,
                temperature    = MODEL_CFG.temperature,
                do_sample      = True,
            )
        raw = _processor.decode(
            output_ids[0][input_len:],
            skip_special_tokens = False,
        )
    except Exception as exc:
        logger.error("Inference error: %s — using keyword fallback.", exc)
        return _keyword_fallback(headlines)

    logger.debug("Raw output (%d chars): %s", len(raw), raw[:600])

    parsed = _extract_json(raw)
    if parsed is None:
        logger.warning("JSON parse failed — using keyword fallback.")
        return _keyword_fallback(headlines)

    # Validate and sanitise
    valid = []
    for item in parsed:
        try:
            sector = str(item["sector"]).strip()
            if sector not in LSE_SECTORS:
                logger.debug("Unknown sector '%s' — skipping.", sector)
                continue

            confidence          = float(item.get("confidence", 0.0))
            disruption_type     = str(item.get("disruption_type", "Regulatory / Policy Shift"))
            disruption_strength = float(item.get("disruption_strength", 0.5))
            time_to_impact      = int(item.get("time_to_impact_weeks", 3))
            propagation         = str(item.get("propagation", ""))[:300]
            invalidation_risk   = str(item.get("invalidation_risk", ""))[:200]
            rationale           = str(item.get("rationale", ""))[:300]

            # Clamp floats
            confidence          = max(0.0, min(1.0, confidence))
            disruption_strength = max(0.0, min(1.0, disruption_strength))
            time_to_impact      = max(1,   min(12, time_to_impact))

            valid.append({
                "sector":               sector,
                "confidence":           round(confidence, 4),
                "disruption_type":      disruption_type,
                "disruption_strength":  round(disruption_strength, 4),
                "time_to_impact_weeks": time_to_impact,
                "propagation":          propagation,
                "invalidation_risk":    invalidation_risk,
                "rationale":            rationale,
            })
        except (KeyError, TypeError, ValueError) as exc:
            logger.debug("Malformed entry %s: %s", item, exc)

    valid.sort(key=lambda x: x["confidence"], reverse=True)
    logger.info("LLM emitted %d valid swing signals.", len(valid))
    return valid
