"""
Quarterly Universe check.

Compares the Sector Map (sector_map.json) with the current FTSE 100, FTSE 250
and S&P 500 constituents on Wikipedia, and validates the map's contents.
Run it after each FTSE Russell review (March, June, September, December) and
now and then for the S&P 500, which changes more often:

    python -m lse_stock_analysis.universe_check

It only reports.  To apply the changes, edit sector_map.json by hand:
add an entry (with a Primary Sector from config.LSE_SECTORS, or null if the
stock fits none) for each stock that joined, and delete each stock that left.
For S&P 500 joiners the report prints a suggested entry (GICS sub-industry
mapping from sp500_map.py) that can be pasted in; add a sub-industry to
GICS_TO_SECTOR if the report says one is missing.

Needs requests, pandas and lxml (not required by the weekly pipeline).
"""
import importlib.util
import io
import sys
from pathlib import Path

from .sp500_map import UK_ONLY_SECTORS, entry_for, yahoo_symbol
from .universe import load_sector_map

_WIKI_PAGES = {
    "FTSE 100": "https://en.wikipedia.org/wiki/FTSE_100_Index",
    "FTSE 250": "https://en.wikipedia.org/wiki/FTSE_250_Index",
}
_SP500_PAGE = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_CONFIG_PATH = Path(__file__).resolve().parents[1] / "financial_market_news_analyzer" / "config.py"


def yahoo_ticker(wiki_ticker: str) -> str:
    """Wikipedia/LSE ticker → Yahoo ticker, e.g. 'BT.A' → 'BT-A.L'."""
    return wiki_ticker.strip().rstrip(".").replace(".", "-") + ".L"


def fetch_constituents() -> dict[str, dict]:
    """
    Current constituents from Wikipedia.

    Returns
    -------
    dict[str, dict]
        Yahoo ticker → {"company": str, "index": str}
    """
    import pandas as pd
    import requests

    out: dict[str, dict] = {}
    for index, url in _WIKI_PAGES.items():
        resp = requests.get(url, headers={"User-Agent": "robotrader universe check"}, timeout=30)
        resp.raise_for_status()
        for table in pd.read_html(io.StringIO(resp.text)):
            cols = {str(c).lower(): c for c in table.columns}
            ticker_col = next((c for k, c in cols.items() if "ticker" in k or "epic" in k), None)
            company_col = next((c for k, c in cols.items() if "company" in k), None)
            if ticker_col is None or company_col is None or len(table) < 80:
                continue
            for _, row in table.iterrows():
                out[yahoo_ticker(str(row[ticker_col]))] = {"company": str(row[company_col]).strip(), "index": index}
            break
        else:
            raise RuntimeError(f"No constituents table found at {url}")

    resp = requests.get(_SP500_PAGE, headers={"User-Agent": "robotrader universe check"}, timeout=30)
    resp.raise_for_status()
    sp500 = next((t for t in pd.read_html(io.StringIO(resp.text)) if {"Symbol", "Security", "GICS Sub-Industry"} <= set(t.columns)), None)
    if sp500 is None or len(sp500) < 400:
        raise RuntimeError(f"No S&P 500 constituents table found at {_SP500_PAGE}")
    for _, row in sp500.iterrows():
        out[yahoo_symbol(row["Symbol"])] = {
            "company": str(row["Security"]).strip(), "index": "S&P 500",
            "gics_sector": str(row["GICS Sector"]), "gics_sub_industry": str(row["GICS Sub-Industry"]),
        }
    return out


def diff_universe(current: dict[str, dict], sector_map: dict[str, dict]) -> dict[str, list]:
    """
    Compare current constituents with the Sector Map.

    Returns
    -------
    dict with keys:
        joined         — in the index, not in the Sector Map
        left           — in the Sector Map, no longer in the index
        moved          — in both, but the index changed (e.g. promoted to FTSE 100)
    Each value is a sorted list of (ticker, company, detail) tuples.
    """
    joined = [(t, e["company"], e["index"]) for t, e in current.items() if t not in sector_map]
    left = [(t, e["company"], e["index"]) for t, e in sector_map.items() if t not in current]
    moved = [
        (t, e["company"], f'{sector_map[t]["index"]} -> {e["index"]}')
        for t, e in current.items()
        if t in sector_map and sector_map[t]["index"] != e["index"]
    ]
    return {"joined": sorted(joined), "left": sorted(left), "moved": sorted(moved)}


def load_sector_names() -> set[str]:
    """The 31 Sector names in the scout's taxonomy (config.LSE_SECTORS)."""
    spec = importlib.util.spec_from_file_location("scout_config", _CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.LSE_SECTORS)


def validate_sector_map(sector_map: dict[str, dict], sector_names: set[str]) -> list[str]:
    """Return a list of problems with the Sector Map (empty if it is valid)."""
    problems = []
    for ticker, entry in sector_map.items():
        index = entry.get("index")
        if index in _WIKI_PAGES:
            if not ticker.endswith(".L"):
                problems.append(f"{ticker}: not a Yahoo LSE ticker")
        elif index == "S&P 500":
            if ticker.endswith(".L"):
                problems.append(f"{ticker}: an S&P 500 stock cannot have an LSE ticker")
            if entry.get("primary_sector") in UK_ONLY_SECTORS or set(entry.get("secondary_sectors", [])) & UK_ONLY_SECTORS:
                problems.append(f"{ticker}: US stocks are not mapped into the UK-specific Sectors")
        else:
            problems.append(f"{ticker}: unknown index {index!r}")
        primary = entry.get("primary_sector")
        secondary = entry.get("secondary_sectors", [])
        if primary is not None and primary not in sector_names:
            problems.append(f"{ticker}: unknown primary sector {primary!r}")
        for s in secondary:
            if s not in sector_names:
                problems.append(f"{ticker}: unknown secondary sector {s!r}")
        if primary is not None and primary in secondary:
            problems.append(f"{ticker}: primary sector also listed as secondary")
        if primary is None and secondary:
            problems.append(f"{ticker}: unmapped stock has secondary sectors")
    return problems


def uninvestable_sectors(sector_map: dict[str, dict], sector_names: set[str]) -> list[str]:
    """Sectors that no stock has as its Primary Sector."""
    used = {e["primary_sector"] for e in sector_map.values() if e["primary_sector"]}
    return sorted(sector_names - used)


def main() -> int:
    sector_map = load_sector_map()
    sector_names = load_sector_names()

    problems = validate_sector_map(sector_map, sector_names)
    unmapped = sum(1 for e in sector_map.values() if e["primary_sector"] is None)
    print(f"Sector Map: {len(sector_map)} stocks, {len(sector_map) - unmapped} mapped, {unmapped} unmapped")
    print(f"Uninvestable Sectors: {uninvestable_sectors(sector_map, sector_names) or 'none'}")
    for p in problems:
        print(f"  PROBLEM  {p}")

    current = fetch_constituents()
    changes = diff_universe(current, sector_map)
    for label, rows in changes.items():
        print(f"\n{label.upper()} ({len(rows)})")
        for ticker, company, detail in rows:
            print(f"  {ticker:<9} {company}  [{detail}]")
            if label == "joined" and detail == "S&P 500":
                e = current[ticker]
                try:
                    print("    suggested entry:", entry_for(ticker, company, e["gics_sector"], e["gics_sub_industry"]))
                except KeyError as exc:
                    print(f"    cannot suggest an entry: {exc}")

    return 1 if problems or any(changes.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
