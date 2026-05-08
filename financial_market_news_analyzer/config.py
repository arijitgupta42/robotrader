"""
config.py — Central configuration for the LSE Sector Scout.

Rewritten for MEDIUM-TERM SWING detection (2–6 week horizon).
The philosophy: we are not reacting to today's price action.
We are looking for structural disruptions — policy shifts, supply-chain
dislocations, macro regime changes, geopolitical shocks — that the market
has begun pricing but not yet fully digested, similar to how the market
treated semiconductors during export-control escalations.
"""
import os
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# LSE / FTSE ICB Sector Taxonomy + Sector-Specific Keywords
#
# Granular sub-sectors rather than broad ICB buckets, so signals name
# specific stock clusters the LLM (and the trader) can act on.
# ---------------------------------------------------------------------------

LSE_SECTORS: Dict[str, List[str]] = {

    # ---- Technology --------------------------------------------------------
    "Semiconductors & EDA": [
        "semiconductor", "chip", "wafer", "foundry", "fabless", "EDA",
        "ARM Holdings", "TSMC", "Nvidia", "Intel", "ASML", "export controls",
        "chip shortage", "advanced packaging", "HBM", "CoWoS",
    ],
    "Cloud & SaaS": [
        "cloud", "SaaS", "software-as-a-service", "Sage Group", "Aveva",
        "Micro Focus", "Azure", "AWS", "GCP", "enterprise software",
        "subscription revenue", "ARR", "churn",
    ],
    "Cybersecurity": [
        "cybersecurity", "cyber attack", "ransomware", "data breach",
        "NCC Group", "Darktrace", "zero-trust", "NCSC", "vulnerability",
        "critical infrastructure attack",
    ],
    "AI Infrastructure": [
        "artificial intelligence", "AI", "large language model", "LLM",
        "GPU cluster", "data centre AI", "inference", "training compute",
        "AI chips", "AI regulation", "foundation model",
    ],
    "Data Centres & Digital Infrastructure": [
        "data centre", "colocation", "hyperscaler", "Digital 9 Infrastructure",
        "Segro tech", "power demand data centre", "cooling", "rack density",
        "network capacity",
    ],

    # ---- Financials --------------------------------------------------------
    "UK Retail Banks": [
        "Lloyds Banking", "NatWest", "Barclays retail", "Halifax",
        "mortgage", "net interest margin", "NIM", "loan impairment",
        "BoE base rate", "Bank of England", "credit card default",
        "consumer credit", "savings rate",
    ],
    "Investment Banks & Brokers": [
        "Barclays investment bank", "HSBC", "Standard Chartered",
        "investment banking", "M&A advisory", "ECM", "DCM", "trading revenue",
        "capital markets", "IPO pipeline",
    ],
    "Insurance": [
        "Aviva", "Legal & General", "Prudential", "Admiral",
        "Direct Line", "Beazley", "Lloyd's of London", "reinsurance",
        "combined ratio", "catastrophe loss", "premium rate",
    ],
    "Asset Management & Wealth": [
        "asset management", "fund manager", "abrdn", "Schroders",
        "Man Group", "Intermediate Capital", "AUM", "flows",
        "passive vs active", "fee compression",
    ],
    "Fintech & Payments": [
        "fintech", "payments", "Wise", "Network International",
        "open banking", "buy now pay later", "BNPL", "digital wallet",
        "interchange", "PSR",
    ],

    # ---- Energy ------------------------------------------------------------
    "Integrated Oil & Gas": [
        "BP", "Shell", "TotalEnergies", "oil price", "brent crude",
        "upstream", "downstream", "refining margin", "OPEC", "LNG",
        "North Sea", "windfall tax", "energy profits levy",
    ],
    "Renewables & Clean Energy": [
        "wind farm", "solar", "offshore wind", "Orsted", "SSE renewables",
        "green hydrogen", "CfD auction", "capacity market", "Vattenfall",
        "energy transition", "net zero", "National Grid ESO",
    ],
    "Oil Field Services": [
        "Petrofac", "John Wood Group", "Hunting", "Expro",
        "oilfield services", "drilling rig", "subsea", "well completion",
        "capex upstream",
    ],

    # ---- Healthcare --------------------------------------------------------
    "Pharma & Biotech": [
        "AstraZeneca", "GSK", "Hikma", "Indivior",
        "drug approval", "FDA", "MHRA", "clinical trial phase",
        "patent cliff", "biosimilar", "GLP-1", "oncology",
    ],
    "Medical Devices & Services": [
        "Smith+Nephew", "ConvaTec", "Spectranetics", "Electrocomponents health",
        "NHS contract", "surgical robot", "orthopaedic", "wound care",
        "diagnostics", "point-of-care",
    ],

    # ---- Consumer ----------------------------------------------------------
    "UK General Retail": [
        "Marks & Spencer", "Next", "B&M", "Primark", "Dunelm",
        "footfall", "like-for-like sales", "LFL", "consumer confidence",
        "real wages", "discretionary spend",
    ],
    "Luxury & Lifestyle": [
        "Burberry", "Watches of Switzerland", "Mulberry",
        "luxury goods", "China consumption", "aspirational spending",
        "duty free", "tourism spend",
    ],
    "Travel, Leisure & Hospitality": [
        "easyJet", "IAG", "Jet2", "TUI", "Whitbread",
        "hotel occupancy", "yield management", "load factor",
        "holiday booking", "staycation", "cruise",
    ],
    "Grocery & Food Retail": [
        "Tesco", "J Sainsbury", "Ocado", "Marks & Spencer food",
        "grocery inflation", "own-label", "shrinkflation",
        "food price index", "discounters", "Aldi", "Lidl pressure",
    ],
    "Consumer Staples & FMCG": [
        "Unilever", "Reckitt", "Diageo", "British American Tobacco",
        "Imperial Brands", "pricing power", "volume growth",
        "input cost", "commodity inflation FMCG", "emerging markets FMCG",
    ],

    # ---- Industrials -------------------------------------------------------
    "Aerospace & Defence": [
        "BAE Systems", "Rolls-Royce", "Babcock", "QinetiQ", "Ultra Electronics",
        "defence budget", "NATO spending", "Eurofighter", "Type 26 frigate",
        "government defence contract", "geopolitical rearmament",
    ],
    "Engineering & Industrials": [
        "Weir Group", "IMI", "Melrose Industries", "GKN",
        "industrial automation", "reshoring manufacturing",
        "capex cycle", "order book", "book-to-bill", "supply chain nearshoring",
    ],
    "Logistics & Transport": [
        "Royal Mail", "International Distributions Services", "DHL UK",
        "parcel volumes", "last-mile delivery", "freight rates",
        "rail freight", "port throughput", "haulage", "e-commerce logistics",
    ],

    # ---- Materials ---------------------------------------------------------
    "Diversified Mining": [
        "Rio Tinto", "Anglo American", "Glencore", "BHP",
        "iron ore", "copper price", "thermal coal", "metallurgical coal",
        "China steel demand", "mining capex",
    ],
    "Specialty Metals & Battery Materials": [
        "Antofagasta", "Centamin", "Hochschild", "Polymetal",
        "lithium", "cobalt", "nickel", "rare earth", "EV battery supply",
        "critical minerals", "CBAM", "battery gigafactory",
    ],

    # ---- Real Estate -------------------------------------------------------
    "Commercial & Logistics REITs": [
        "Segro", "Tritax Big Box", "LondonMetric", "Warehouse REIT",
        "logistics property", "last-mile warehouse", "rent indexation",
        "vacancy rate industrial",
    ],
    "Retail & Office REITs": [
        "Land Securities", "British Land", "Hammerson", "Derwent London",
        "office vacancy", "hybrid working", "retail park",
        "footfall retail property", "yield expansion commercial",
    ],
    "Housebuilders": [
        "Taylor Wimpey", "Persimmon", "Barratt Developments", "Bellway",
        "Berkeley Group", "Help to Buy", "planning reform",
        "mortgage approval", "house price index", "build cost inflation",
    ],

    # ---- Utilities ---------------------------------------------------------
    "Electricity & Grid": [
        "National Grid", "SSE", "Drax", "Centrica",
        "electricity price", "grid investment", "transmission", "ofgem",
        "capacity market", "power purchase agreement", "battery storage grid",
    ],
    "Water": [
        "Severn Trent", "United Utilities", "Pennon", "South West Water",
        "ofwat", "price review PR24", "leakage target", "wastewater",
        "regulatory settlement water",
    ],

    # ---- Telecoms ----------------------------------------------------------
    "UK Telecoms & Broadband": [
        "BT Group", "Vodafone UK", "Virgin Media O2", "TalkTalk",
        "fibre rollout", "FTTP", "Openreach", "5G spectrum",
        "mobile consolidation", "ofcom", "broadband subsidy",
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
# Reddit Subreddit Configuration
#
# Each entry configures one subreddit to scrape.
#
# Fields:
#   subreddit  : subreddit name (no r/ prefix)
#   sorts      : list of sort modes to fetch — "hot" captures current buzz,
#                "top" with t=week catches the week's highest-conviction posts
#   limit      : max posts per sort request (Reddit API hard cap = 100)
#   min_score  : discard posts below this net-upvote count to filter noise.
#                Lower for niche UK subs (less traffic), higher for WSB-scale.
#
# Subreddit selection rationale:
#   UKInvesting / UKPersonalFinance — retail LSE-focused discussion
#   investing / stocks              — global but high DD quality; catches
#                                     macro/sector moves before UK press does
#   wallstreetbets                  — useful as a CONTRARIAN signal when
#                                     crowded; also surfaces momentum names
#                                     that often have LSE-dual-listed exposure
#   SecurityAnalysis                — institutional-quality long-form DD
#   Economics                       — macro regime commentary
#   Commodities / energy            — commodity price thesis; maps to Mining,
#                                     Oil & Gas, Renewables sectors directly
#   options / thetagang             — options flow can lead equity moves by
#                                     days; surfaces near-term catalyst plays
# ---------------------------------------------------------------------------

REDDIT_SUBREDDITS: List[Dict] = [
    # --- UK-focused ---
    # Both "hot" and "top" sorts are used now that OAuth bypasses the Lambda
    # IP blocks that caused 403s on /top with the unauthenticated API.
    # "hot"       → current buzz and active discussions this week
    # "top" t=week → the week's highest-conviction posts by community vote
    {"subreddit": "UKInvesting",         "sorts": ["hot", "top"], "limit": 30, "min_score": 20},
    {"subreddit": "UKPersonalFinance",   "sorts": ["hot"],        "limit": 20, "min_score": 50},

    # --- Global / high-quality DD ---
    {"subreddit": "investing",           "sorts": ["hot", "top"], "limit": 35, "min_score": 200},
    {"subreddit": "stocks",              "sorts": ["hot", "top"], "limit": 35, "min_score": 150},
    {"subreddit": "SecurityAnalysis",    "sorts": ["hot", "top"], "limit": 25, "min_score": 50},

    # --- Macro / commodities ---
    {"subreddit": "Economics",           "sorts": ["hot"],        "limit": 20, "min_score": 100},
    {"subreddit": "Commodities",         "sorts": ["hot", "top"], "limit": 20, "min_score": 30},
    {"subreddit": "energy",              "sorts": ["hot"],        "limit": 15, "min_score": 30},

    # --- Sentiment / momentum (contrarian + momentum signals) ---
    {"subreddit": "wallstreetbets",      "sorts": ["hot"],        "limit": 25, "min_score": 500},
    {"subreddit": "options",             "sorts": ["hot"],        "limit": 20, "min_score": 100},
    {"subreddit": "thetagang",           "sorts": ["hot"],        "limit": 15, "min_score": 50},
]


@dataclass
class RedditConfig:
    max_posts_per_cycle: int = 200
    # Fallback min_score if not specified per-subreddit.
    # RSS feeds do not expose scores so this is not actively used —
    # quality filtering is delegated entirely to the LLM pass.
    default_min_score:     int = 50
    # Minimum bullish_conviction from reddit_analyzer to include in consolidation
    min_reddit_conviction: float = 0.40


REDDIT_CFG = RedditConfig()


# ---------------------------------------------------------------------------
# OpenRouter Model Config
#
# OPENROUTER_MODELS is the local fallback used when SSM is unreachable.
# To change models without redeploying the Lambda, edit the SSM parameter:
#   /sector-scout/openrouter-models  (comma-separated, same order)
# ---------------------------------------------------------------------------

OPENROUTER_MODELS: List[str] = [
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "google/gemma-3-27b-it:free",
    "openrouter/owl-alpha",
]

# Retry delays in seconds for transient HTTP errors (429 / 5xx / timeout).
OPENROUTER_RETRY_DELAYS: List[int] = [15, 30, 60]


def load_openrouter_models() -> List[str]:
    """
    Load the OpenRouter model list from SSM Parameter Store at runtime.
    This allows updating the model list without redeploying the Lambda —
    just edit /sector-scout/openrouter-models in the AWS console.

    Falls back to OPENROUTER_MODELS (defined above) if SSM is unreachable
    or the parameter is missing/empty.
    """
    try:
        import boto3
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
        resp = ssm.get_parameter(Name="/sector-scout/openrouter-models")
        models = [m.strip() for m in resp["Parameter"]["Value"].split(",") if m.strip()]
        if models:
            return models
        logger.warning("SSM: openrouter-models is empty — using config fallback.")
    except Exception as exc:
        logger.warning("SSM: could not load openrouter-models (%s: %s) — using config fallback.",
                       type(exc).__name__, exc)
    return OPENROUTER_MODELS


# ---------------------------------------------------------------------------
# Scheduler Settings
# ---------------------------------------------------------------------------

@dataclass
class SchedulerConfig:
    max_headlines_per_cycle: int = 500
    min_confidence:          float = 0.55   # lowered slightly — consolidator re-filters
    min_disruption_strength: float = 0.50   # lowered slightly — consolidator re-filters


SCHEDULER_CFG = SchedulerConfig()
