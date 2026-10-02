import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lse_stock_analysis.get_stock_data import _INDEX_TICKERS
from lse_stock_analysis.universe import SECTOR_MAP_PATH, load_sector_map, universe_tickers
from lse_stock_analysis.universe_check import (
    diff_universe,
    load_sector_names,
    uninvestable_sectors,
    validate_sector_map,
    yahoo_ticker,
)

SECTOR_NAMES = load_sector_names()


def test_taxonomy_has_31_sectors():
    assert len(SECTOR_NAMES) == 31


def test_sector_map_is_valid():
    assert validate_sector_map(load_sector_map(), SECTOR_NAMES) == []


def test_universe_is_the_ftse_350():
    assert len(universe_tickers()) == 350
    assert len(universe_tickers("FTSE 100")) == 100
    assert len(universe_tickers("FTSE 250")) == 250
    assert set(universe_tickers("FTSE 100")).isdisjoint(universe_tickers("FTSE 250"))


def test_every_universe_stock_has_a_sector_map_entry():
    assert set(_INDEX_TICKERS["FTSE 350"]) == set(load_sector_map())


def test_uninvestable_sectors_are_reported():
    stocks = load_sector_map()
    uninvestable = uninvestable_sectors(stocks, SECTOR_NAMES)
    assert "AI Infrastructure" in uninvestable
    assert "Housebuilders" not in uninvestable


def test_yahoo_ticker_conversion():
    assert yahoo_ticker("BT.A") == "BT-A.L"
    assert yahoo_ticker("III") == "III.L"
    assert yahoo_ticker(" CCH ") == "CCH.L"


def test_validate_catches_bad_entries():
    bad = {
        "AAA.L": {"index": "FTSE 100", "primary_sector": "Not A Sector", "secondary_sectors": []},
        "BBB": {"index": "FTSE 100", "primary_sector": None, "secondary_sectors": []},
        "CCC.L": {"index": "FTSE 250", "primary_sector": "Water", "secondary_sectors": ["Water"]},
        "DDD.L": {"index": "FTSE 250", "primary_sector": None, "secondary_sectors": ["Water"]},
        "EEE.L": {"index": "FTSE 600", "primary_sector": "Water", "secondary_sectors": ["Imaginary"]},
    }
    problems = validate_sector_map(bad, SECTOR_NAMES)
    assert len(problems) == 6
    assert any("AAA.L" in p and "unknown primary" in p for p in problems)
    assert any("BBB" in p and "not a Yahoo" in p for p in problems)
    assert any("CCC.L" in p and "also listed" in p for p in problems)
    assert any("DDD.L" in p and "unmapped" in p for p in problems)
    assert any("EEE.L" in p and "unknown index" in p for p in problems)
    assert any("EEE.L" in p and "unknown secondary" in p for p in problems)


def test_diff_universe_reports_joined_left_and_moved():
    sector_map = {
        "OLD.L": {"company": "Old Co", "index": "FTSE 250"},
        "STAY.L": {"company": "Stay Co", "index": "FTSE 250"},
        "UP.L": {"company": "Up Co", "index": "FTSE 250"},
    }
    current = {
        "STAY.L": {"company": "Stay Co", "index": "FTSE 250"},
        "NEW.L": {"company": "New Co", "index": "FTSE 250"},
        "UP.L": {"company": "Up Co", "index": "FTSE 100"},
    }
    d = diff_universe(current, sector_map)
    assert d["joined"] == [("NEW.L", "New Co", "FTSE 250")]
    assert d["left"] == [("OLD.L", "Old Co", "FTSE 250")]
    assert d["moved"] == [("UP.L", "Up Co", "FTSE 250 -> FTSE 100")]


def test_sector_map_file_is_valid_json_with_meta():
    doc = json.loads(Path(SECTOR_MAP_PATH).read_text(encoding="utf-8"))
    assert set(doc) == {"_meta", "stocks"}
