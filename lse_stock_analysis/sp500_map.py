"""
Sector Map entries for the S&P 500.

A US stock's Primary Sector comes from its GICS sub-industry (the table on Wikipedia's list of S&P 500
companies), mapped onto the scout's existing 31 Sectors, with a few manual overrides for stocks whose
business fits a Sector better than their sub-industry does (AI hardware, data-centre power, EDA, clean energy).

US stocks are never mapped into the UK-specific Sectors (UK_ONLY_SECTORS): their Sector Signals are driven by
UK news (the BoE, UK high-street demand, UK telecoms regulation), so a signal on one of them still picks UK
stocks only.  A sub-industry that fits no other Sector maps to None, an Unmapped Stock, which is never selected.

Refresh after S&P index changes with `python -m lse_stock_analysis.universe_check`, which reports joiners
and leavers and suggests an entry (via `entry_for`) for each joiner.
"""
from typing import Optional

UK_ONLY_SECTORS = frozenset({"UK Retail Banks", "UK General Retail", "UK Telecoms & Broadband"})

# GICS sub-industry -> scout Sector (None: fits none of the global-theme Sectors)
GICS_TO_SECTOR: dict[str, Optional[str]] = {
    # Communication Services
    "Advertising": None, "Broadcasting": None, "Cable & Satellite": None,
    "Integrated Telecommunication Services": None, "Interactive Home Entertainment": None,
    "Interactive Media & Services": None, "Movies & Entertainment": None, "Publishing": None,
    "Wireless Telecommunication Services": None,
    # Consumer Discretionary
    "Apparel Retail": None,
    "Apparel, Accessories & Luxury Goods": "Luxury & Lifestyle",
    "Automobile Manufacturers": None, "Automotive Parts & Equipment": None, "Automotive Retail": None,
    "Broadline Retail": None,
    "Casinos & Gaming": "Travel, Leisure & Hospitality",
    "Computer & Electronics Retail": None, "Consumer Electronics": None, "Distributors": None,
    "Footwear": "Luxury & Lifestyle",
    "Home Improvement Retail": None,
    "Homebuilding": "Housebuilders",
    "Homefurnishing Retail": None,
    "Hotels, Resorts & Cruise Lines": "Travel, Leisure & Hospitality",
    "Leisure Products": "Travel, Leisure & Hospitality",
    "Other Specialty Retail": None,
    "Restaurants": "Travel, Leisure & Hospitality",
    "Specialized Consumer Services": None,
    # Consumer Staples
    "Agricultural Products & Services": "Consumer Staples & FMCG",
    "Consumer Staples Merchandise Retail": "Grocery & Food Retail",
    "Distillers & Vintners": "Consumer Staples & FMCG",
    "Food Distributors": "Grocery & Food Retail",
    "Food Retail": "Grocery & Food Retail",
    "Household Products": "Consumer Staples & FMCG",
    "Packaged Foods & Meats": "Consumer Staples & FMCG",
    "Personal Care Products": "Consumer Staples & FMCG",
    "Soft Drinks & Non-alcoholic Beverages": "Consumer Staples & FMCG",
    "Tobacco": "Consumer Staples & FMCG",
    # Energy
    "Integrated Oil & Gas": "Integrated Oil & Gas",
    "Oil & Gas Equipment & Services": "Oil Field Services",
    "Oil & Gas Exploration & Production": "Integrated Oil & Gas",
    "Oil & Gas Refining & Marketing": "Integrated Oil & Gas",
    "Oil & Gas Storage & Transportation": "Integrated Oil & Gas",
    # Financials
    "Asset Management & Custody Banks": "Asset Management & Wealth",
    "Consumer Finance": "Fintech & Payments",
    "Diversified Banks": "Investment Banks & Brokers",
    "Financial Exchanges & Data": "Investment Banks & Brokers",
    "Insurance Brokers": "Insurance",
    "Investment Banking & Brokerage": "Investment Banks & Brokers",
    "Life & Health Insurance": "Insurance",
    "Multi-Sector Holdings": None,
    "Multi-line Insurance": "Insurance",
    "Property & Casualty Insurance": "Insurance",
    "Regional Banks": None,
    "Reinsurance": "Insurance",
    "Transaction & Payment Processing Services": "Fintech & Payments",
    # Health Care
    "Biotechnology": "Pharma & Biotech",
    "Health Care Distributors": "Medical Devices & Services",
    "Health Care Equipment": "Medical Devices & Services",
    "Health Care Facilities": "Medical Devices & Services",
    "Health Care Services": "Medical Devices & Services",
    "Health Care Supplies": "Medical Devices & Services",
    "Health Care Technology": "Medical Devices & Services",
    "Life Sciences Tools & Services": "Pharma & Biotech",
    "Managed Health Care": "Medical Devices & Services",
    "Pharmaceuticals": "Pharma & Biotech",
    # Industrials
    "Aerospace & Defense": "Aerospace & Defence",
    "Agricultural & Farm Machinery": "Engineering & Industrials",
    "Air Freight & Logistics": "Logistics & Transport",
    "Building Products": "Engineering & Industrials",
    "Cargo Ground Transportation": "Logistics & Transport",
    "Construction & Engineering": "Engineering & Industrials",
    "Construction Machinery & Heavy Transportation Equipment": "Engineering & Industrials",
    "Data Processing & Outsourced Services": None,
    "Diversified Support Services": "Engineering & Industrials",
    "Electrical Components & Equipment": "Engineering & Industrials",
    "Environmental & Facilities Services": "Engineering & Industrials",
    "Heavy Electrical Equipment": "Engineering & Industrials",
    "Human Resource & Employment Services": None,
    "Industrial Conglomerates": "Engineering & Industrials",
    "Industrial Machinery & Supplies & Components": "Engineering & Industrials",
    "Passenger Airlines": "Travel, Leisure & Hospitality",
    "Passenger Ground Transportation": "Logistics & Transport",
    "Rail Transportation": "Logistics & Transport",
    "Research & Consulting Services": None,
    "Trading Companies & Distributors": "Engineering & Industrials",
    # Information Technology
    "Application Software": "Cloud & SaaS",
    "Communications Equipment": None,
    "Electronic Components": None,
    "Electronic Equipment & Instruments": None,
    "Electronic Manufacturing Services": None,
    "IT Consulting & Other Services": "Cloud & SaaS",
    "Internet Services & Infrastructure": "Data Centres & Digital Infrastructure",
    "Semiconductor Materials & Equipment": "Semiconductors & EDA",
    "Semiconductors": "Semiconductors & EDA",
    "Systems Software": "Cloud & SaaS",
    "Technology Distributors": "Cloud & SaaS",
    "Technology Hardware, Storage & Peripherals": None,
    # Materials
    "Commodity Chemicals": None,
    "Construction Materials": None,
    "Copper": "Specialty Metals & Battery Materials",
    "Fertilizers & Agricultural Chemicals": None,
    "Gold": "Specialty Metals & Battery Materials",
    "Industrial Gases": None,
    "Metal, Glass & Plastic Containers": None,
    "Paper & Plastic Packaging Products & Materials": None,
    "Specialty Chemicals": None,
    "Steel": "Diversified Mining",
    # Real Estate
    "Data Center REITs": "Data Centres & Digital Infrastructure",
    "Health Care REITs": "Commercial & Logistics REITs",
    "Hotel & Resort REITs": "Commercial & Logistics REITs",
    "Industrial REITs": "Commercial & Logistics REITs",
    "Multi-Family Residential REITs": None,
    "Office REITs": "Retail & Office REITs",
    "Other Specialized REITs": None,
    "Real Estate Services": None,
    "Retail REITs": "Retail & Office REITs",
    "Self-Storage REITs": "Commercial & Logistics REITs",
    "Single-Family Residential REITs": None,
    "Telecom Tower REITs": "Data Centres & Digital Infrastructure",
    "Timber REITs": None,
    # Utilities
    "Electric Utilities": "Electricity & Grid",
    "Gas Utilities": "Electricity & Grid",
    "Independent Power Producers & Energy Traders": "Electricity & Grid",
    "Multi-Utilities": "Electricity & Grid",
    "Water Utilities": "Water",
}

# Yahoo ticker -> (primary Sector, secondary Sectors, why).  The few stocks whose business fits a Sector better
# than their GICS sub-industry does; everything else follows GICS_TO_SECTOR.
OVERRIDES: dict[str, tuple[Optional[str], list[str], str]] = {
    "NVDA": ("AI Infrastructure", ["Semiconductors & EDA"], "AI accelerators and GPU clusters"),
    "AMD": ("AI Infrastructure", ["Semiconductors & EDA"], "AI accelerators and data-centre CPUs"),
    "AVGO": ("AI Infrastructure", ["Semiconductors & EDA"], "custom AI chips and AI networking"),
    "SMCI": ("AI Infrastructure", [], "AI server systems"),
    "DELL": ("AI Infrastructure", [], "AI server systems"),
    "HPE": ("AI Infrastructure", [], "AI and data-centre systems"),
    "ANET": ("AI Infrastructure", [], "AI data-centre networking"),
    "VRT": ("Data Centres & Digital Infrastructure", [], "data-centre power and cooling"),
    "CDNS": ("Semiconductors & EDA", [], "chip-design (EDA) software"),
    "SNPS": ("Semiconductors & EDA", [], "chip-design (EDA) software"),
    "CRWD": ("Cybersecurity", [], "cybersecurity software"),
    "PANW": ("Cybersecurity", [], "cybersecurity software"),
    "FTNT": ("Cybersecurity", [], "cybersecurity software"),
    "GEN": ("Cybersecurity", [], "consumer cybersecurity software"),
    "GEV": ("Electricity & Grid", [], "grid and power-generation equipment"),
    "FSLR": ("Renewables & Clean Energy", [], "solar modules"),
    "NEE": ("Renewables & Clean Energy", ["Electricity & Grid"], "largest US renewables developer"),
    "ALB": ("Specialty Metals & Battery Materials", [], "lithium"),
    "UBER": ("Logistics & Transport", [], "mobility and delivery"),
}

def yahoo_symbol(symbol: str) -> str:
    """S&P 500 symbol -> Yahoo ticker, e.g. 'BRK.B' -> 'BRK-B'."""
    return str(symbol).strip().replace(".", "-")


def entry_for(symbol: str, company: str, gics_sector: str, gics_sub_industry: str) -> dict:
    """The Sector Map entry for one S&P 500 stock."""
    ticker = yahoo_symbol(symbol)
    if ticker in OVERRIDES:
        primary, secondary, why = OVERRIDES[ticker]
        note = f"Override: {why}"
    else:
        if gics_sub_industry not in GICS_TO_SECTOR:
            raise KeyError(f"GICS sub-industry {gics_sub_industry!r} ({ticker}) is not in GICS_TO_SECTOR; add it")
        primary, secondary, note = GICS_TO_SECTOR[gics_sub_industry], [], None
    entry = {
        "company": str(company).strip(), "index": "S&P 500", "market": "US", "currency": "USD",
        "gics_sector": gics_sector, "gics_sub_industry": gics_sub_industry,
        "primary_sector": primary, "secondary_sectors": list(secondary),
    }
    if primary is None:
        entry["note"] = f"No global-theme Sector fits ({gics_sub_industry})"
    elif note:
        entry["note"] = note
    return entry
