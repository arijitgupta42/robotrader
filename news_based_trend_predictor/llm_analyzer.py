"""
llm_analyzer.py — LangChain + Gemma 4 E2B-it with structured output.

Why LangChain structured output?
---------------------------------
Instead of prompting the model to "return JSON only" and then running
six extraction strategies to parse whatever it emits, we use LangChain's
`.with_structured_output()` backed by Gemma 4's native function-calling.

The flow is:
  1. A Pydantic schema (AnalysisOutput) defines the exact structure we want.
  2. LangChain converts the schema into a tool definition and injects it
     into the prompt automatically.
  3. Gemma 4 E2B-it responds by calling that tool with the structured data.
  4. LangChain deserialises the response directly into a validated Pydantic
     object — no regex, no bracket-walking, no six-strategy fallback.

If validation fails (the model hallucinated a wrong type, missing field,
etc.) Pydantic raises a ValidationError, which we catch and fall back to
the keyword heuristic.

LangChain setup
---------------
    pip install langchain langchain-huggingface

Model loading: we still load via transformers/HuggingFace directly and
wrap with HuggingFacePipeline → ChatHuggingFace so we keep full control
over quantisation, device placement, and dtype.
"""

from __future__ import annotations

import logging
import warnings
from typing import Dict, List, Literal, Optional

import torch
from pydantic import BaseModel, Field, field_validator

from config import DISRUPTION_CATEGORIES, LSE_SECTORS, MODEL_CFG, SCHEDULER_CFG
from news_fetcher import Headline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic output schema
# ---------------------------------------------------------------------------

_SECTOR_NAMES     = list(LSE_SECTORS.keys())
_DISRUPTION_NAMES = list(DISRUPTION_CATEGORIES.keys())

# Build Literal types dynamically from config so they stay in sync
SectorLiteral     = Literal[tuple(_SECTOR_NAMES)]       # type: ignore[valid-type]
DisruptionLiteral = Literal[tuple(_DISRUPTION_NAMES)]   # type: ignore[valid-type]


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
            # Soft fix: try a case-insensitive match before rejecting
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
# Lazy singleton — model + LangChain chain loaded once
# ---------------------------------------------------------------------------

_chain = None   # LangChain runnable: ChatHuggingFace | with_structured_output


def _load_chain() -> None:
    global _chain
    if _chain is not None:
        return

    from transformers import AutoProcessor, AutoModelForMultimodalLM, pipeline, BitsAndBytesConfig
    from langchain_huggingface import HuggingFacePipeline, ChatHuggingFace

    # --- GPU diagnostic ---
    logger.info("PyTorch version  : %s", torch.__version__)
    logger.info("CUDA available   : %s", torch.cuda.is_available())
    if torch.cuda.is_available():
        logger.info("CUDA version     : %s", torch.version.cuda)
        for i in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(i)
            logger.info("GPU %d            : %s  (%.1f GB VRAM)", i, props.name, props.total_memory / 1024**3)
    elif torch.backends.mps.is_available():
        logger.info("Apple MPS        : available")
    else:
        warnings.warn(
            "No GPU detected — Gemma 4 E2B on CPU will be very slow. "
            "Fix: pip install torch --index-url https://download.pytorch.org/whl/cu121",
            RuntimeWarning,
        )

    logger.info("Loading %s …", MODEL_CFG.model_id)

    dtype_map = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}
    torch_dtype = dtype_map.get(MODEL_CFG.torch_dtype, torch.bfloat16)

    load_kwargs: dict = {
        "device_map":          "auto",
        "attn_implementation": MODEL_CFG.attn_implementation,
        "dtype":         torch_dtype,
    }

    if MODEL_CFG.use_4bit_quantisation and torch.cuda.is_available():
        load_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
        )
        load_kwargs.pop("torch_dtype", None)
        logger.info("4-bit NF4 quantisation enabled.")

    if not torch.cuda.is_available() and not torch.backends.mps.is_available():
        load_kwargs["dtype"] = torch.float32

    model = AutoModelForMultimodalLM.from_pretrained(
        MODEL_CFG.model_id,
        **load_kwargs
    ).eval()

    processor = AutoProcessor.from_pretrained(MODEL_CFG.model_id)

    logger.info(
        "Model loaded on: %s",
        model.hf_device_map if hasattr(model, "hf_device_map") else "cpu",
    )

    # --- Extract the underlying tokenizer from Gemma4Processor ---
    # AutoProcessor for Gemma 4 returns a Gemma4Processor which wraps a
    # tokenizer internally. The transformers pipeline() and LangChain both
    # call .pad_token_id directly on whatever is passed as tokenizer=, so
    # we must pass the inner tokenizer, not the processor itself.
    tokenizer = getattr(processor, "tokenizer", processor)

    # Gemma 4's tokenizer may not have pad_token set; use eos_token as pad
    # (standard practice for decoder-only models that have no explicit pad).
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
        logger.info("pad_token_id not set — using eos_token_id (%d) as pad.",
                    tokenizer.eos_token_id)

    # Also propagate to the model config so generate() doesn't warn
    if model.config.pad_token_id is None:
        model.config.pad_token_id = tokenizer.pad_token_id

    # --- Wrap in a transformers pipeline ---
    hf_pipeline = pipeline(
        task             = "text-generation",
        model            = model,
        tokenizer        = tokenizer,
        max_new_tokens   = MODEL_CFG.max_new_tokens,
        temperature      = MODEL_CFG.temperature,
        do_sample        = True,
        return_full_text = False,   # return only newly generated tokens
    )

    # --- LangChain wrappers ---
    lc_pipeline = HuggingFacePipeline(pipeline=hf_pipeline)
    chat_model  = ChatHuggingFace(
        llm      = lc_pipeline,
        tokenizer = tokenizer,
        model_id  = MODEL_CFG.model_id,
    )

    # --- Bind structured output schema ---
    # LangChain converts AnalysisOutput into a tool definition and injects
    # it into the prompt.  The model responds by calling that tool with
    # structured data; LangChain validates it against the Pydantic schema.
    _chain = chat_model.with_structured_output(AnalysisOutput)
    logger.info("LangChain structured-output chain ready.")


# ---------------------------------------------------------------------------
# Prompt construction
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

Then call the provided tool with your structured findings.
Only include sectors with confidence >= 0.4.
Return an empty signals list if no genuine multi-week disruption is present.
"""


def _build_messages(headlines: List[Headline]) -> list:
    from langchain_core.messages import SystemMessage, HumanMessage
    lines = [f"{i+1}. {h.to_text()}" for i, h in enumerate(headlines)]
    user_text = "Recent headlines:\n\n" + "\n".join(lines)
    return [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_text),
    ]


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
            "invalidation_risk":    "Heuristic — validate manually.",
            "rationale":            f"Keyword-based: {kw_hits} sector hits in headlines.",
        })
    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results[:5]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyse_headlines(headlines: List[Headline]) -> List[Dict]:
    """
    Run Gemma 4 E2B-it via LangChain structured output over the supplied
    headlines and return a validated list of swing signal dicts.

    Each dict contains:
        sector, confidence, disruption_type, disruption_strength,
        time_to_impact_weeks, propagation, invalidation_risk, rationale

    Falls back to keyword heuristic on any error.
    """
    if not headlines:
        logger.warning("analyse_headlines called with empty list.")
        return []

    _load_chain()

    messages = _build_messages(headlines)
    logger.info("Invoking structured-output chain on %d headlines …", len(headlines))

    try:
        result: AnalysisOutput = _chain.invoke(messages)
    except Exception as exc:
        logger.error("LangChain chain invocation failed: %s — using fallback.", exc)
        return _keyword_fallback(headlines)

    if not isinstance(result, AnalysisOutput):
        logger.error(
            "Chain returned unexpected type %s — using fallback.", type(result)
        )
        return _keyword_fallback(headlines)

    signals = sorted(result.signals, key=lambda s: s.confidence, reverse=True)
    logger.info("Structured output returned %d validated signals.", len(signals))

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
