import json
from pathlib import Path

# ---------------------------------------------------------------------------
# The Universe is every stock in sector_map.json: the FTSE 100 and FTSE 250
# (market "LSE", prices in pence) plus the S&P 500 (market "US", prices in
# US dollars).  The Sector Map is the single source of truth, so a stock can
# never be in the Universe without a Sector Map entry.
#
# Update sector_map.json after each quarterly index review (FTSE Russell
# Mar/Jun/Sep/Dec; S&P changes as they happen) — run
# `python -m lse_stock_analysis.universe_check` to list the stocks that
# joined or left.
# ---------------------------------------------------------------------------

SECTOR_MAP_PATH = Path(__file__).with_name("sector_map.json")

MARKETS = ("LSE", "US")
CURRENCIES = {"LSE": "GBp", "US": "USD"}       # LSE prices are quoted in pence


def load_sector_map(path: Path = SECTOR_MAP_PATH) -> dict[str, dict]:
    """
    Load the Sector Map.

    Returns
    -------
    dict[str, dict]
        Yahoo ticker (e.g. 'BT-A.L') → entry with keys:
        company, index ('FTSE 100' | 'FTSE 250'), icb_sector,
        primary_sector (str, or None for an Unmapped Stock),
        secondary_sectors (list[str]) and optionally note.
    """
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["stocks"]


def market_of(ticker: str) -> str:
    """'LSE' for a Yahoo ticker ending in .L, else 'US'."""
    return "LSE" if ticker.endswith(".L") else "US"


def currency_of(ticker: str) -> str:
    """Quote currency of a Universe stock: 'GBp' (pence) for LSE stocks, 'USD' for US stocks."""
    return CURRENCIES[market_of(ticker)]


def universe_tickers(index: str | None = None, market: str | None = None) -> list[str]:
    """
    Yahoo tickers in the Universe, sorted.

    Parameters
    ----------
    index : str, optional
        'FTSE 100', 'FTSE 250' or 'S&P 500' to restrict to one index.
        None (default) returns the whole Universe.
    market : str, optional
        'LSE' or 'US' to restrict to one market.
    """
    stocks = load_sector_map()
    return sorted(t for t, e in stocks.items()
                  if (index is None or e["index"] == index) and (market is None or market_of(t) == market))
