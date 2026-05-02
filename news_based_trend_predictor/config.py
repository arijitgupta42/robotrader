"""
config.py — Central configuration for the LSE Sector Scout.

Rewritten for MEDIUM-TERM SWING detection (2–6 week horizon).
The philosophy: we are not reacting to today's price action.
We are looking for structural disruptions — policy shifts, supply-chain
dislocations, macro regime changes, geopolitical shocks — that the market
has begun pricing but not yet fully digested, similar to how the market
treated semiconductors during export-control escalations.
"""

from dataclasses import dataclass
from typing import Dict, List


# ---------------------------------------------------------------------------
# LSE / FTSE ICB Sector Taxonomy + Sector-Specific Keywords
# ---------------------------------------------------------------------------

LSE_SECTORS: Dict[str, List[str]] = {
    "Technology": [
        "semiconductor", "chip", "AI", "artificial intelligence", "cloud",
        "cybersecurity", "data centre", "ARM", "Sage", "Aveva", "SaaS",
        "export controls", "TSMC", "Nvidia", "supply chain tech",
    ],
    "Financials": [
        "bank", "insurance", "asset management", "HSBC", "Barclays",
        "Lloyds", "NatWest", "Standard Chartered", "Prudential", "Aviva",
        "interest rate", "BoE", "Bank of England", "credit spreads",
        "gilt", "yield curve", "Basel", "capital requirements", "fintech",
    ],
    "Energy": [
        "oil", "gas", "energy", "renewables", "wind", "solar", "BP",
        "Shell", "North Sea", "LNG", "brent", "crude", "OPEC",
        "carbon price", "windfall tax", "energy transition", "hydrogen",
    ],
    "Healthcare": [
        "pharma", "biotech", "drug approval", "clinical trial", "FDA",
        "MHRA", "AstraZeneca", "GSK", "Smith+Nephew", "Hikma", "vaccine",
        "NHS", "GLP-1", "weight loss drug", "patent cliff", "biosimilar",
    ],
    "Consumer Discretionary": [
        "retail", "luxury", "automotive", "travel", "hospitality",
        "Marks & Spencer", "Next", "JD Sports", "easyJet", "IAG",
        "Burberry", "consumer confidence", "real wages", "credit card",
    ],
    "Consumer Staples": [
        "food", "beverage", "household", "tobacco", "Unilever", "Diageo",
        "Reckitt", "British American Tobacco", "grocery", "FMCG",
        "input costs", "commodity prices", "pricing power",
    ],
    "Industrials": [
        "manufacturing", "aerospace", "defence", "logistics", "engineering",
        "Rolls-Royce", "BAE Systems", "Babcock", "infrastructure",
        "supply chain", "reshoring", "nearshoring", "government contract",
        "defence spending", "NATO",
    ],
    "Materials": [
        "mining", "metals", "chemicals", "steel", "copper", "lithium",
        "Rio Tinto", "Anglo American", "Glencore", "Antofagasta",
        "commodities", "rare earth", "EV battery", "critical minerals",
    ],
    "Real Estate": [
        "REIT", "property", "housing", "commercial real estate",
        "Segro", "Land Securities", "British Land", "Rightmove",
        "mortgage rates", "housebuilder", "Taylor Wimpey", "Persimmon",
        "office vacancy", "logistics property",
    ],
    "Utilities": [
        "electricity", "water", "grid", "National Grid",
        "Severn Trent", "United Utilities", "SSE", "Centrica",
        "ofwat", "ofgem", "regulation", "price cap", "network investment",
    ],
    "Telecommunications": [
        "telecom", "5G", "broadband", "BT Group", "Vodafone",
        "fibre rollout", "spectrum auction", "mobile", "consolidation",
        "ofcom",
    ],
}

# ---------------------------------------------------------------------------
# Disruption Category Taxonomy
#
# These are the LENSES through which the LLM should read the news.
# Each maps to a hypothesis about why a sector might reprice over
# a 2–6 week window, not just in today's session.
# ---------------------------------------------------------------------------

DISRUPTION_CATEGORIES: Dict[str, str] = {
    "Supply Chain Dislocation": (
        "Tariffs, sanctions, port disruptions, or reshoring policies that "
        "structurally change input costs or revenue routes for an entire sector."
    ),
    "Regulatory / Policy Shift": (
        "New legislation, central bank guidance, Ofgem/Ofwat decisions, "
        "MHRA approvals, or government spending pledges that change the "
        "competitive or cost landscape for a sector over the coming months."
    ),
    "Macro Regime Change": (
        "Shifts in BoE rate expectations, UK inflation surprises, gilt "
        "yield moves, or sterling devaluations that systematically reprice "
        "rate-sensitive or commodity-priced sectors."
    ),
    "Geopolitical Shock": (
        "Conflict escalation, trade bloc fragmentation, sanctions, or "
        "diplomatic ruptures with material trade consequences for UK-listed "
        "companies exposed to that region or supply chain."
    ),
    "Technology / Adoption Inflection": (
        "A product launch, platform shift, or new AI/semiconductor "
        "capability that begins to obsolete incumbents or dramatically "
        "expands addressable market — the 'semiconductor moment'."
    ),
    "Earnings / Guidance Divergence": (
        "A cluster of beats or misses across a sector suggesting "
        "consensus estimates are systematically wrong, setting up a "
        "multi-week re-rating as the street catches up."
    ),
    "Commodity Price Inflection": (
        "A sustained move in oil, gas, copper, lithium, or agricultural "
        "commodities that has not yet fully fed through to the equity "
        "valuations of exposed LSE-listed companies."
    ),
    "M&A / Consolidation Wave": (
        "A deal or rumoured bid that signals sector-wide consolidation "
        "premium, bidding wars, or strategic asset revaluation likely to "
        "persist for weeks."
    ),
}

# ---------------------------------------------------------------------------
# News Sources — weighted toward macro, policy, and structural disruption
# ---------------------------------------------------------------------------

RSS_FEEDS: List[Dict[str, str]] = [
    # --- UK Financial / Markets (verified working) ---
    {"name": "BBC Business",             "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "BBC UK Politics",          "url": "https://feeds.bbci.co.uk/news/politics/rss.xml"},
    {"name": "Guardian Business",        "url": "https://www.theguardian.com/uk/business/rss"},
    {"name": "Guardian Economics",       "url": "https://www.theguardian.com/business/economics/rss"},
    {"name": "City A.M.",                "url": "https://www.cityam.com/feed/"},
    {"name": "Sky News Business",        "url": "https://feeds.skynews.com/feeds/rss/business.xml"},
    {"name": "Independent Business",     "url": "https://www.independent.co.uk/news/business/rss"},
    {"name": "Telegraph Business",       "url": "https://www.telegraph.co.uk/business/rss.xml"},

    # --- Market Data / RNS ---
    {"name": "Proactive Investors UK",   "url": "https://www.proactiveinvestors.co.uk/feed"},
    {"name": "Investegate RNS",          "url": "https://www.investegate.co.uk/rss.aspx"},

    # --- Global Macro (trade flows / commodities affect LSE multinationals) ---
    {"name": "AP Business",             "url": "https://feeds.apnews.com/apnews/business"},
    {"name": "AP Top News",             "url": "https://feeds.apnews.com/apnews/topnews"},
    {"name": "Yahoo Finance",           "url": "https://finance.yahoo.com/news/rssindex"},

    # --- Thomson Reuters Investor Relations (corporate press releases) ---
    # Note: this is TR's own IR feed — good for macro signals from financial
    # data/analytics industry moves, but not general market news.
    {"name": "Thomson Reuters IR",      "url": "https://ir.thomsonreuters.com/rss/news-releases.xml"},
]

# HTML scrape targets (static pages, no JS rendering needed)
SCRAPE_TARGETS: List[Dict[str, str]] = [
    {"name": "London Stock Exchange News",  "url": "https://www.londonstockexchange.com/news"},
    {"name": "Hargreaves Lansdown News",    "url": "https://www.hl.co.uk/news/market-news"},
    {"name": "Bank of England News",        "url": "https://www.bankofengland.co.uk/news"},
]


# ---------------------------------------------------------------------------
# Model Settings
#
# Correct Gemma 4 E2B API usage confirmed from:
#   https://huggingface.co/google/gemma-4-E2B-it
#
# Key differences from Gemma 3 / other models:
#   - Uses AutoProcessor (not AutoTokenizer) + AutoModelForCausalLM
#   - Chat template applied via processor.apply_chat_template()
#   - enable_thinking flag controls chain-of-thought reasoning mode
#   - Thinking output is wrapped in <|channel>thought\n...<channel|>
#     and must be stripped before parsing the JSON answer
#   - Native system role support (new in Gemma 4)
# ---------------------------------------------------------------------------

@dataclass
class ModelConfig:
    # Confirmed model ID: capital E2B
    model_id: str = "google/gemma-4-E2B-it"

    # bfloat16 is the recommended dtype for Gemma 4 (per HF model card)
    torch_dtype: str = "bfloat16"

    # Set True only if VRAM < 8 GB (requires bitsandbytes)
    use_4bit_quantisation: bool = False

    # Enable Gemma 4's native chain-of-thought before the JSON answer.
    # We WANT this: it allows the model to reason through the disruption
    # thesis before committing to a sector signal. The thought block is
    # stripped from the output before JSON parsing.
    enable_thinking: bool = True

    # Extra headroom for thinking tokens + JSON output
    max_new_tokens: int = 1024

    # Slightly higher temperature so the model explores non-obvious sectors
    temperature: float = 0.4

    # Recommended attention implementation for Gemma 4 (per HF docs)
    attn_implementation: str = "sdpa"


# ---------------------------------------------------------------------------
# Scheduler Settings
# ---------------------------------------------------------------------------

@dataclass
class SchedulerConfig:
    # More headlines for richer context (medium-term needs more signal)
    max_headlines_per_cycle: int = 150

    # Minimum LLM confidence to emit a SectorSignal
    min_confidence: float = 0.60

    # Minimum disruption strength to consider a thesis actionable
    min_disruption_strength: float = 0.55


# ---------------------------------------------------------------------------
# Shared singleton instances
# ---------------------------------------------------------------------------

MODEL_CFG     = ModelConfig()
SCHEDULER_CFG = SchedulerConfig()
