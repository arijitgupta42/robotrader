import json
from pathlib import Path

# ---------------------------------------------------------------------------
# The Universe is every stock in sector_map.json: the FTSE 100 plus the
# FTSE 250.  The Sector Map is the single source of truth, so a stock can
# never be in the Universe without a Sector Map entry.
#
# Update sector_map.json after each quarterly FTSE Russell review
# (Mar/Jun/Sep/Dec) — run `python -m lse_stock_analysis.universe_check`
# to list the stocks that joined or left.
# ---------------------------------------------------------------------------

SECTOR_MAP_PATH = Path(__file__).with_name("sector_map.json")


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


def universe_tickers(index: str | None = None) -> list[str]:
    """
    Yahoo tickers in the Universe, sorted.

    Parameters
    ----------
    index : str, optional
        'FTSE 100' or 'FTSE 250' to restrict to one index.
        None (default) returns the whole FTSE 350.
    """
    stocks = load_sector_map()
    return sorted(t for t, e in stocks.items() if index is None or e["index"] == index)
