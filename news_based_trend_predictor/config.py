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
# News Sources
# ---------------------------------------------------------------------------

RSS_FEEDS: List[Dict[str, str]] = [
    # --- UK Financial / Markets ---
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

    # --- Global Macro ---
    {"name": "AP Business",             "url": "https://feeds.apnews.com/apnews/business"},
    {"name": "AP Top News",             "url": "https://feeds.apnews.com/apnews/topnews"},
    {"name": "Yahoo Finance",           "url": "https://finance.yahoo.com/news/rssindex"},
    {"name": "Thomson Reuters IR",      "url": "https://ir.thomsonreuters.com/rss/news-releases.xml"},

    # --- BoE ---
    {"name": "Bank of England News",        "url": "https://www.bankofengland.co.uk/rss/news"},
    {"name": "Bank of England Publications","url": "https://www.bankofengland.co.uk/rss/publications"},
    {"name": "Bank of England Speeches",    "url": "https://www.bankofengland.co.uk/rss/speeches"},

    # --- Investing.com UK ---
    {"name": "Investing.com UK Stock News", "url": "https://uk.investing.com/rss/news_25.rss"},
    {"name": "Investing.com UK Economy",    "url": "https://uk.investing.com/rss/news_14.rss"},
    {"name": "Investing.com UK Commodities","url": "https://uk.investing.com/rss/news_11.rss"},
]


# ---------------------------------------------------------------------------
# OpenRouter Model Priority List
#
# Models are tried in order. All are free tier (:free suffix).
# Selection criteria: 262K+ context, native structured output (json_schema),
# configurable reasoning/thinking mode.
#
# Primary:   Gemma 4 31B  — dense 31B, best reasoning quality, 262K ctx
# Secondary: Gemma 4 26B  — MoE (3.8B active), near-identical quality, 262K ctx
# Tertiary:  Nemotron 3 Super — 120B MoE (12B active), 1M ctx theoretical
# Fallback:  Nemotron Nano Omni — different provider lineage, 256K ctx
# ---------------------------------------------------------------------------

OPENROUTER_MODELS: List[str] = [
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

# Retry delays in seconds between attempts on the same model (429 / timeout).
# After these are exhausted the next model in the list is tried.
OPENROUTER_RETRY_DELAYS: List[int] = [15, 30, 60]


# ---------------------------------------------------------------------------
# Scheduler Settings
# ---------------------------------------------------------------------------

@dataclass
class SchedulerConfig:
    # Headlines passed to the LLM per cycle
    max_headlines_per_cycle: int = 100

    # Minimum LLM confidence to emit a SectorSignal
    min_confidence: float = 0.60

    # Minimum disruption strength to consider a thesis actionable
    min_disruption_strength: float = 0.55


# ---------------------------------------------------------------------------
# Shared singleton instance
# ---------------------------------------------------------------------------

SCHEDULER_CFG = SchedulerConfig()
