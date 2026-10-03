import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stock_selection_lambda"))

import email_report  # noqa: E402
from lse_stock_analysis import prices as px  # noqa: E402
from lse_stock_analysis import sp500_map  # noqa: E402
from lse_stock_analysis.selection import SectorSelection, build_snapshot  # noqa: E402
from lse_stock_analysis.universe import (  # noqa: E402
    MARKETS, currency_of, load_sector_map, market_of, universe_tickers,
)
from lse_stock_analysis.universe_check import load_sector_names, validate_sector_map  # noqa: E402

STOCKS = load_sector_map()
SECTORS = load_sector_names()
US = {t: e for t, e in STOCKS.items() if e["index"] == "S&P 500"}


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# The mapping
# ---------------------------------------------------------------------------

def test_every_gics_target_is_a_real_sector_and_never_a_uk_only_one():
    for sub, sector in sp500_map.GICS_TO_SECTOR.items():
        assert sector is None or sector in SECTORS, sub
        assert sector not in sp500_map.UK_ONLY_SECTORS, sub
    for ticker, (primary, secondary, _) in sp500_map.OVERRIDES.items():
        assert primary in SECTORS and set(secondary) <= SECTORS
        assert primary not in sp500_map.UK_ONLY_SECTORS and not set(secondary) & sp500_map.UK_ONLY_SECTORS
        assert ticker in US, f"override for {ticker}, which is not in the S&P 500 map"


def test_the_committed_map_covers_503_us_stocks_all_valid():
    assert len(US) == 503
    assert validate_sector_map(STOCKS, SECTORS) == []
    assert all(e["market"] == "US" and e["currency"] == "USD" for e in US.values())
    assert not any(t.endswith(".L") for t in US)
    assert not {e["primary_sector"] for e in US.values()} & sp500_map.UK_ONLY_SECTORS


def test_every_committed_subindustry_is_in_the_mapping():
    assert {e["gics_sub_industry"] for e in US.values()} <= set(sp500_map.GICS_TO_SECTOR)


def test_the_empty_global_theme_sectors_now_have_stocks():
    by_sector = {}
    for t, e in US.items():
        by_sector.setdefault(e["primary_sector"], []).append(t)
    for sector in ("AI Infrastructure", "Data Centres & Digital Infrastructure", "Semiconductors & EDA", "Cybersecurity"):
        assert len(by_sector.get(sector, [])) >= 3, sector
    assert "NVDA" in by_sector["AI Infrastructure"] and "CRWD" in by_sector["Cybersecurity"]
    assert STOCKS["NVDA"]["secondary_sectors"] == ["Semiconductors & EDA"]


def test_entry_for_uses_gics_then_overrides_and_rejects_unknown_subindustries():
    e = sp500_map.entry_for("DHI", "D.R. Horton", "Consumer Discretionary", "Homebuilding")
    assert e["primary_sector"] == "Housebuilders" and e["index"] == "S&P 500" and e["market"] == "US"
    e = sp500_map.entry_for("NVDA", "Nvidia", "Information Technology", "Semiconductors")
    assert e["primary_sector"] == "AI Infrastructure" and e["note"].startswith("Override")
    e = sp500_map.entry_for("GOOGL", "Alphabet", "Communication Services", "Interactive Media & Services")
    assert e["primary_sector"] is None and "No global-theme Sector fits" in e["note"]
    with pytest.raises(KeyError, match="not in GICS_TO_SECTOR"):
        sp500_map.entry_for("ZZZ", "Zed", "Utilities", "Space Utilities")


def test_yahoo_symbols_for_share_classes():
    assert sp500_map.yahoo_symbol("BRK.B") == "BRK-B" and sp500_map.yahoo_symbol(" BF.B ") == "BF-B"
    assert "BRK-B" in US


def test_validation_rejects_us_stocks_in_uk_only_sectors_and_lse_tickers_in_the_sp500():
    bad = {"AAA": {"index": "S&P 500", "primary_sector": "UK Retail Banks", "secondary_sectors": []},
           "BBB.L": {"index": "S&P 500", "primary_sector": None, "secondary_sectors": []},
           "CCC": {"index": "S&P 500", "primary_sector": "Water", "secondary_sectors": ["UK General Retail"]}}
    problems = validate_sector_map(bad, SECTORS)
    assert any("AAA" in p and "UK-specific" in p for p in problems)
    assert any("BBB.L" in p and "cannot have an LSE ticker" in p for p in problems)
    assert any("CCC" in p and "UK-specific" in p for p in problems)


def test_market_and_currency_helpers():
    assert market_of("BP.L") == "LSE" and market_of("NVDA") == "US" and market_of("BRK-B") == "US"
    assert currency_of("BP.L") == "GBp" and currency_of("NVDA") == "USD"
    assert set(universe_tickers(market="US")) == set(US) and MARKETS == ("LSE", "US")


# ---------------------------------------------------------------------------
# Prices per market
# ---------------------------------------------------------------------------

def test_cutoff_is_per_market():
    monday_evening = utc(2026, 10, 5, 18, 0)                          # London 19:00 BST (closed), New York 14:00 EDT (open)
    assert px.cutoff_date(monday_evening) == date(2026, 10, 5) == px.cutoff_date(monday_evening, "LSE")
    assert px.cutoff_date(monday_evening, "US") == date(2026, 10, 4)
    assert px.cutoff_date(utc(2026, 10, 5, 20, 59), "US") == date(2026, 10, 4)        # 16:59 EDT
    assert px.cutoff_date(utc(2026, 10, 5, 21, 0), "US") == date(2026, 10, 5)         # 17:00 EDT
    assert px.cutoff_date(utc(2026, 10, 4, 6, 0), "US") == date(2026, 10, 3)          # the scout's Sunday run
    assert px.cutoff_date(utc(2026, 12, 7, 22, 0), "US") == date(2026, 12, 7)         # 17:00 EST in winter


def bars(end, n=80, start_price=100.0, jump_at=None, factor=1.0):
    idx = pd.bdate_range(end=end, periods=n)
    close = np.full(n, start_price)
    if jump_at is not None:
        close[jump_at:] *= factor
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1000}, index=idx)


def test_each_stock_is_cut_at_its_own_markets_cutoff():
    raw = {"BP.L": bars("2026-10-05"), "NVDA": bars("2026-10-05")}
    result = px.clean_prices(raw, {"LSE": date(2026, 10, 5), "US": date(2026, 10, 2)}, ["BP.L", "NVDA"])
    assert result.last_bar == {"BP.L": date(2026, 10, 5), "NVDA": date(2026, 10, 2)}
    assert result.cutoff_date == date(2026, 10, 5) and result.cutoff_for("NVDA") == date(2026, 10, 2)


def test_a_single_cutoff_date_still_applies_to_every_market():
    result = px.clean_prices({"BP.L": bars("2026-10-05"), "NVDA": bars("2026-10-05")}, date(2026, 10, 2), ["BP.L", "NVDA"])
    assert set(result.last_bar.values()) == {date(2026, 10, 2)}


def test_the_pence_pounds_fix_applies_to_lse_stocks_only():
    jumped = lambda: bars("2026-10-02", jump_at=40, factor=100.0)    # noqa: E731
    result = px.clean_prices({"BP.L": jumped(), "NVDA": jumped()}, date(2026, 10, 2), ["BP.L", "NVDA"])
    assert result.unit_fixed == {"BP.L": 1}                          # rescaled
    assert "NVDA" not in result.unit_fixed and "NVDA" in result.price_anomalies      # a real 100x move is an anomaly, not a unit slip


def test_staleness_is_judged_against_the_stocks_own_cutoff():
    raw = {"BP.L": bars("2026-10-02"), "NVDA": bars("2026-09-25")}
    result = px.clean_prices(raw, {"LSE": date(2026, 10, 2), "US": date(2026, 10, 2)}, ["BP.L", "NVDA"])
    assert "NVDA" in result.stale and "BP.L" not in result.stale


def test_fetch_uses_the_earliest_cutoff_for_the_download_window_and_cleans_per_market():
    seen = {}

    def downloader(tickers, start, end):
        seen.update(start=start, end=end)
        return {"BP.L": bars("2026-10-05"), "NVDA": bars("2026-10-05")}

    result = px.fetch_universe_prices(utc(2026, 10, 5, 18, 0), tickers=["BP.L", "NVDA"], downloader=downloader)
    assert seen["start"] == date(2026, 10, 4) - pd.Timedelta(days=180) and seen["end"] == date(2026, 10, 6)
    assert result.cutoffs == {"LSE": date(2026, 10, 5), "US": date(2026, 10, 4)}
    assert result.last_bar["BP.L"] == date(2026, 10, 5) and result.last_bar["NVDA"] == date(2026, 10, 2)   # Friday: the last bar before the US cutoff


# ---------------------------------------------------------------------------
# Snapshot and email
# ---------------------------------------------------------------------------

def test_snapshot_rows_carry_market_currency_and_their_markets_cutoff():
    prices = px.clean_prices({"BP.L": bars("2026-10-05"), "NVDA": bars("2026-10-05")},
                             {"LSE": date(2026, 10, 5), "US": date(2026, 10, 2)}, ["BP.L", "NVDA"])
    sector_map = {k: STOCKS[k] for k in ("BP.L", "NVDA")}
    snap = build_snapshot(utc(2026, 10, 5, 18, 0), [], {}, prices, [], sector_map=sector_map).set_index("ticker")
    assert snap.loc["BP.L", "market"] == "LSE" and snap.loc["BP.L", "currency"] == "GBp"
    assert snap.loc["NVDA", "market"] == "US" and snap.loc["NVDA", "currency"] == "USD"
    assert snap.loc["BP.L", "price_cutoff"] == "2026-10-05" and snap.loc["NVDA", "price_cutoff"] == "2026-10-02"


def test_picks_show_market_and_the_right_currency():
    assert email_report.price_text(1234.5, "PSN.L") == "1,234.50p" and email_report.price_text(123.456, "NVDA") == "$123.46"
    pick = {"ticker": "NVDA", "company": "Nvidia", "swing_setup": "momentum", "setup_grade": "A", "risk_score": 2,
            "stop_loss_pct": 4.0, "max_position_pct": 20.0, "close": 187.5}
    sel = SectorSelection(sector="AI Infrastructure", confidence=0.8, convergence_type="News-Led", picks_allowed=2,
                          investable=True, candidate_count=7, picks=[pick])
    p = px.PriceFetchResult(cutoff_date=date(2026, 10, 2), requested=["NVDA"], cutoffs={"LSE": date(2026, 10, 2), "US": date(2026, 10, 2)})
    p.data = {"NVDA": pd.DataFrame()}
    sig = {"sector": "AI Infrastructure", "confidence": 0.8, "convergence_type": "News-Led"}
    _, html_body, text_body = email_report.build_report_email("2026-W40", utc(2026, 10, 4, 6, 0), {"signals": [sig]}, [sel], p)
    assert ">Mkt<" in html_body and ">US<" in html_body and "$187.50" in html_body
    assert "close $187.50 (US)" in text_body


def test_header_shows_the_us_cutoff_only_when_it_differs():
    same = px.PriceFetchResult(cutoff_date=date(2026, 10, 2), cutoffs={"LSE": date(2026, 10, 2), "US": date(2026, 10, 2)})
    assert email_report.cutoff_text(same) == "prices to 2026-10-02 close"
    differ = px.PriceFetchResult(cutoff_date=date(2026, 10, 5), cutoffs={"LSE": date(2026, 10, 5), "US": date(2026, 10, 2)})
    assert email_report.cutoff_text(differ) == "prices to 2026-10-05 close (US 2026-10-02)"


def test_a_lagging_bar_warning_is_per_market():
    p = px.PriceFetchResult(cutoff_date=date(2026, 10, 2), requested=["A.L", "NVDA"],
                            cutoffs={"LSE": date(2026, 10, 2), "US": date(2026, 10, 2)})
    p.data = {"A.L": pd.DataFrame(), "NVDA": pd.DataFrame()}
    p.last_bar = {"A.L": date(2026, 10, 2), "NVDA": date(2026, 10, 1)}          # only the US bar is a day behind
    warnings = " | ".join(email_report.build_warnings(p))
    assert "1 US stock(s) is 2026-10-01" in warnings and "LSE stock(s)" not in warnings
