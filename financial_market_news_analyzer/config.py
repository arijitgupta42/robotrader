"""
config.py — Central configuration for the Sector Scout.

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
# Sector Taxonomy + Sector-Specific Keywords
#
# The scout's 31 Sectors, used for both markets in the Universe (the FTSE 350
# and the S&P 500).  Granular sub-sectors rather than broad index buckets, so
# signals name specific stock clusters the LLM (and the trader) can act on.
# Three Sectors are UK-specific (their signals are driven by UK news, so they
# only ever pick FTSE 350 stocks): UK Retail Banks, UK General Retail and
# UK Telecoms & Broadband.  Every other Sector is global.
#
# The keywords are matched case-insensitively as substrings of headline and
# post text, for the per-Sector source-diversity score, the keyword fallback
# when the news LLM fails, and the evidence attached to each signal.  They mix
# UK and US company names with the sector's themes; avoid short or common
# strings that match inside other words (e.g. "UPS", "RTX", "KLA").
# ---------------------------------------------------------------------------

SECTORS: Dict[str, List[str]] = {

    # ---- Technology --------------------------------------------------------
    "Semiconductors & EDA": [
        "semiconductor", "chip", "wafer", "foundry", "fabless", "EDA",
        "ARM Holdings", "TSMC", "Nvidia", "Intel", "ASML", "export controls",
        "chip shortage", "advanced packaging", "HBM", "CoWoS",
        "Broadcom", "Micron Technology", "Qualcomm", "Applied Materials",
        "Lam Research", "KLA Corp", "Synopsys", "Cadence Design",
        "CHIPS Act", "chip tariffs",
    ],
    "Cloud & SaaS": [
        "cloud", "SaaS", "software-as-a-service", "Sage Group", "Aveva",
        "Micro Focus", "Azure", "AWS", "GCP", "enterprise software",
        "subscription revenue", "ARR", "churn",
        "Microsoft", "Salesforce", "Oracle", "ServiceNow", "Adobe",
        "Workday", "Intuit", "cloud backlog", "remaining performance obligation",
    ],
    "Cybersecurity": [
        "cybersecurity", "cyber attack", "ransomware", "data breach",
        "NCC Group", "Darktrace", "zero-trust", "NCSC", "vulnerability",
        "critical infrastructure attack",
        "CrowdStrike", "Palo Alto Networks", "Fortinet", "CISA",
        "state-sponsored hack", "SEC cyber disclosure",
    ],
    "AI Infrastructure": [
        "artificial intelligence", "AI", "large language model", "LLM",
        "GPU cluster", "data centre AI", "inference", "training compute",
        "AI chips", "AI regulation", "foundation model",
        "OpenAI", "Anthropic", "AI capex", "AI spending", "Blackwell",
        "AI data center", "AI power demand",
    ],
    "Data Centres & Digital Infrastructure": [
        "data centre", "colocation", "hyperscaler", "Digital 9 Infrastructure",
        "Segro tech", "power demand data centre", "cooling", "rack density",
        "network capacity",
        "data center", "Equinix", "Digital Realty", "American Tower",
        "Vertiv", "hyperscaler capex", "fibre backbone",
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
        "Goldman Sachs", "Morgan Stanley", "JPMorgan", "Citigroup",
        "Charles Schwab", "Interactive Brokers", "bank stress test",
        "Basel III endgame", "Federal Reserve rate",
    ],
    "Insurance": [
        "Aviva", "Legal & General", "Prudential", "Admiral",
        "Direct Line", "Beazley", "Lloyd's of London", "reinsurance",
        "combined ratio", "catastrophe loss", "premium rate",
        "Progressive Corp", "Chubb", "Allstate", "Travelers Companies", "MetLife",
        "hurricane losses", "P&C pricing", "reinsurance renewal",
    ],
    "Asset Management & Wealth": [
        "asset management", "fund manager", "abrdn", "Schroders",
        "Man Group", "Intermediate Capital", "AUM", "flows",
        "passive vs active", "fee compression",
        "BlackRock", "Blackstone", "KKR", "Apollo Global", "T. Rowe Price",
        "Franklin Templeton", "private credit", "private equity fundraising",
    ],
    "Fintech & Payments": [
        "fintech", "payments", "Wise", "Network International",
        "open banking", "buy now pay later", "BNPL", "digital wallet",
        "interchange", "PSR",
        "Visa Inc", "Mastercard", "PayPal", "Fiserv", "Global Payments",
        "stablecoin", "CFPB", "swipe fees",
    ],

    # ---- Energy ------------------------------------------------------------
    "Integrated Oil & Gas": [
        "BP", "Shell", "TotalEnergies", "oil price", "brent crude",
        "upstream", "downstream", "refining margin", "OPEC", "LNG",
        "North Sea", "windfall tax", "energy profits levy",
        "Exxon", "Chevron", "ConocoPhillips", "WTI", "Permian", "shale",
        "Strategic Petroleum Reserve", "EIA crude inventory", "natural gas price",
    ],
    "Renewables & Clean Energy": [
        "wind farm", "solar", "offshore wind", "Orsted", "SSE renewables",
        "green hydrogen", "CfD auction", "capacity market", "Vattenfall",
        "energy transition", "net zero", "National Grid ESO",
        "NextEra", "First Solar", "Enphase", "Inflation Reduction Act",
        "clean energy tax credit", "solar tariffs",
    ],
    "Oil Field Services": [
        "Petrofac", "John Wood Group", "Hunting", "Expro",
        "oilfield services", "drilling rig", "subsea", "well completion",
        "capex upstream",
        "Schlumberger", "Halliburton", "Baker Hughes", "rig count",
        "shale capex",
    ],

    # ---- Healthcare --------------------------------------------------------
    "Pharma & Biotech": [
        "AstraZeneca", "GSK", "Hikma", "Indivior",
        "drug approval", "FDA", "MHRA", "clinical trial phase",
        "patent cliff", "biosimilar", "GLP-1", "oncology",
        "Eli Lilly", "Pfizer", "Merck", "AbbVie", "Amgen", "Gilead",
        "Regeneron", "Novo Nordisk", "drug pricing", "pharmaceutical tariffs",
        "Medicare drug price negotiation",
    ],
    "Medical Devices & Services": [
        "Smith+Nephew", "ConvaTec", "Spectranetics", "Electrocomponents health",
        "NHS contract", "surgical robot", "orthopaedic", "wound care",
        "diagnostics", "point-of-care",
        "Medtronic", "Intuitive Surgical", "Boston Scientific", "Stryker",
        "UnitedHealth", "Medicare Advantage", "Medicare reimbursement",
        "hospital volumes",
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
        "LVMH", "Kering", "Ralph Lauren", "Tapestry", "Nike",
        "luxury demand", "tariffs on apparel",
    ],
    "Travel, Leisure & Hospitality": [
        "easyJet", "IAG", "Jet2", "TUI", "Whitbread",
        "hotel occupancy", "yield management", "load factor",
        "holiday booking", "staycation", "cruise",
        "Delta Air Lines", "United Airlines", "Marriott", "Hilton",
        "Booking Holdings", "Royal Caribbean", "Carnival", "Las Vegas Sands",
        "TSA throughput", "RevPAR",
    ],
    "Grocery & Food Retail": [
        "Tesco", "J Sainsbury", "Ocado", "Marks & Spencer food",
        "grocery inflation", "own-label", "shrinkflation",
        "food price index", "discounters", "Aldi", "Lidl pressure",
        "Walmart", "Kroger", "Costco", "Sysco", "grocery prices",
        "SNAP benefits",
    ],
    "Consumer Staples & FMCG": [
        "Unilever", "Reckitt", "Diageo", "British American Tobacco",
        "Imperial Brands", "pricing power", "volume growth",
        "input cost", "commodity inflation FMCG", "emerging markets FMCG",
        "Procter & Gamble", "Coca-Cola", "PepsiCo", "Colgate-Palmolive",
        "Mondelez", "Philip Morris", "Kimberly-Clark", "Kraft Heinz",
    ],

    # ---- Industrials -------------------------------------------------------
    "Aerospace & Defence": [
        "BAE Systems", "Rolls-Royce", "Babcock", "QinetiQ", "Ultra Electronics",
        "defence budget", "NATO spending", "Eurofighter", "Type 26 frigate",
        "government defence contract", "geopolitical rearmament",
        "Lockheed Martin", "Raytheon", "Northrop Grumman", "General Dynamics",
        "Boeing", "L3Harris", "Pentagon budget", "defense budget", "NDAA",
        "Golden Dome",
    ],
    "Engineering & Industrials": [
        "Weir Group", "IMI", "Melrose Industries", "GKN",
        "industrial automation", "reshoring manufacturing",
        "capex cycle", "order book", "book-to-bill", "supply chain nearshoring",
        "Caterpillar", "Deere", "Honeywell", "Emerson Electric",
        "Parker Hannifin", "ISM manufacturing", "durable goods orders",
        "infrastructure spending",
    ],
    "Logistics & Transport": [
        "Royal Mail", "International Distributions Services", "DHL UK",
        "parcel volumes", "last-mile delivery", "freight rates",
        "rail freight", "port throughput", "haulage", "e-commerce logistics",
        "United Parcel Service", "FedEx", "Union Pacific", "CSX",
        "Norfolk Southern", "trucking", "freight recession", "port congestion",
    ],

    # ---- Materials ---------------------------------------------------------
    "Diversified Mining": [
        "Rio Tinto", "Anglo American", "Glencore", "BHP",
        "iron ore", "copper price", "thermal coal", "metallurgical coal",
        "China steel demand", "mining capex",
        "Nucor", "Steel Dynamics", "Cleveland-Cliffs", "steel tariffs",
        "steel prices", "Section 232",
    ],
    "Specialty Metals & Battery Materials": [
        "Antofagasta", "Centamin", "Hochschild", "Polymetal",
        "lithium", "cobalt", "nickel", "rare earth", "EV battery supply",
        "critical minerals", "CBAM", "battery gigafactory",
        "Freeport-McMoRan", "Newmont", "Albemarle", "MP Materials",
        "gold price", "copper tariffs", "record gold",
    ],

    # ---- Real Estate -------------------------------------------------------
    "Commercial & Logistics REITs": [
        "Segro", "Tritax Big Box", "LondonMetric", "Warehouse REIT",
        "logistics property", "last-mile warehouse", "rent indexation",
        "vacancy rate industrial",
        "Prologis", "Public Storage", "Extra Space Storage", "Welltower",
        "industrial REIT", "self-storage",
    ],
    "Retail & Office REITs": [
        "Land Securities", "British Land", "Hammerson", "Derwent London",
        "office vacancy", "hybrid working", "retail park",
        "footfall retail property", "yield expansion commercial",
        "Simon Property", "Kimco", "Realty Income", "Boston Properties",
        "office REIT", "mall traffic", "CMBS delinquency",
    ],
    "Housebuilders": [
        "Taylor Wimpey", "Persimmon", "Barratt Developments", "Bellway",
        "Berkeley Group", "Help to Buy", "planning reform",
        "mortgage approval", "house price index", "build cost inflation",
        "D.R. Horton", "Lennar", "PulteGroup", "NVR", "mortgage rates",
        "housing starts", "existing home sales", "homebuilder sentiment",
    ],

    # ---- Utilities ---------------------------------------------------------
    "Electricity & Grid": [
        "National Grid", "SSE", "Drax", "Centrica",
        "electricity price", "grid investment", "transmission", "ofgem",
        "capacity market", "power purchase agreement", "battery storage grid",
        "Constellation Energy", "Vistra", "Duke Energy", "Southern Company",
        "Dominion Energy", "PJM", "FERC", "utility rate case",
        "nuclear power deal",
    ],
    "Water": [
        "Severn Trent", "United Utilities", "Pennon", "South West Water",
        "ofwat", "price review PR24", "leakage target", "wastewater",
        "regulatory settlement water",
        "American Water Works", "Essential Utilities", "PFAS rule",
        "water utility rate case", "EPA drinking water",
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
        "New legislation, central bank guidance, regulator decisions (Ofgem, "
        "Ofwat, FDA, FERC, SEC), drug approvals, tariffs, or government "
        "spending pledges that change the competitive or cost landscape for "
        "a sector over the coming months."
    ),
    "Macro Regime Change": (
        "Shifts in Fed, BoE or ECB rate expectations, inflation surprises, "
        "bond yield moves, or dollar and sterling swings that systematically "
        "reprice rate-sensitive or commodity-priced sectors."
    ),
    "Geopolitical Shock": (
        "Conflict escalation, trade bloc fragmentation, sanctions, or "
        "diplomatic ruptures with material trade consequences for listed "
        "companies (UK or US) exposed to that region or supply chain."
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
        "valuations of exposed listed companies."
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
# Every LLM call in this project goes through OpenRouter to one of these
# models, with reasoning disabled (the request payloads set it): with
# reasoning on, both models intermittently spent the whole max_tokens budget
# thinking and returned empty or truncated JSON.  Live comparison on the
# real news + Reddit pipeline (see the PR that introduced this list):
# openai/gpt-6-luna-pro with reasoning off was the most reliable and gave
# the most stable, highest-confidence signals; deepseek/deepseek-v4.1-flash
# works but its reasoning-off signals were noisier.
# Both are fixed model IDs (no "-latest" alias) so each week's signals can be
# traced to one model version for backtesting.  The list lives only here, so
# git history records which models produced which weeks' signals.
# ---------------------------------------------------------------------------

OPENROUTER_MODELS: List[str] = [
    "openai/gpt-6-luna-pro",
    "deepseek/deepseek-v4.1-flash",
]

# Bump whenever a prompt in llm_analyzer.py, reddit_analyzer.py or consolidator.py (or a
# threshold in this file that shapes the signals) changes.  It is saved with every scout
# result and copied into the Universe Snapshot, so the methodology review can compare
# the weeks before and after a change.
PROMPT_VERSION: str = "2026-10-03.2"

# Retry delays in seconds for transient HTTP errors (429 / 5xx / timeout).
OPENROUTER_RETRY_DELAYS: List[int] = [15, 30, 60]


# ---------------------------------------------------------------------------
# Scheduler Settings
# ---------------------------------------------------------------------------

@dataclass
class SchedulerConfig:
    max_headlines_per_cycle: int = 500
    min_confidence:          float = 0.55   # lowered slightly — consolidator re-filters
    min_disruption_strength: float = 0.50   # lowered slightly — consolidator re-filters


SCHEDULER_CFG = SchedulerConfig()
