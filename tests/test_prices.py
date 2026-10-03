import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lse_stock_analysis import prices
from lse_stock_analysis.universe import universe_tickers
from lse_stock_analysis.prices import (
    MIN_BARS,
    PRICE_ANOMALY_MOVE,
    clean_prices,
    cutoff_date,
    fetch_universe_prices,
    fix_unit_breaks,
    max_abs_daily_move,
)

FIXTURES = Path(__file__).parent / "fixtures"


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def frame(closes, start="2026-04-01", volume=1_000_000):
    """OHLCV frame on consecutive business days, High/Low a hair around the close."""
    index = pd.bdate_range(start, periods=len(closes))
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": volume}, index=index
    )


def load_fixture(name):
    return pd.read_csv(FIXTURES / f"{name}.csv", index_col=0, parse_dates=True)


# ---------------------------------------------------------------------------
# Cutoff: last completed London trading day
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("as_of, expected", [
    # Sunday 06:00 (the scout's usual run): Saturday has no bars, so the data ends on Friday
    (utc(2026, 10, 4, 6, 0), date(2026, 10, 3)),
    # Monday 10:00 BST: market is open, Monday's bar is unfinished -> Sunday (data ends Friday)
    (utc(2026, 10, 5, 9, 0), date(2026, 10, 4)),
    # Monday 15:59 UTC = 16:59 BST: still before 17:00 London
    (utc(2026, 10, 5, 15, 59), date(2026, 10, 4)),
    # Monday 16:00 UTC = 17:00 BST: session complete
    (utc(2026, 10, 5, 16, 0), date(2026, 10, 5)),
    # winter time (GMT): 17:00 London == 17:00 UTC
    (utc(2026, 12, 7, 16, 59), date(2026, 12, 6)),
    (utc(2026, 12, 7, 17, 0), date(2026, 12, 7)),
])
def test_cutoff_date(as_of, expected):
    assert cutoff_date(as_of) == expected


def test_cutoff_date_treats_naive_timestamps_as_utc():
    assert cutoff_date(datetime(2026, 10, 5, 16, 0)) == date(2026, 10, 5)


def test_cutoff_handles_the_clock_change_day():
    # 2026-10-25: BST ends at 02:00. 17:00 that day is GMT (== 17:00 UTC), the day before it was 16:00 UTC.
    assert cutoff_date(utc(2026, 10, 24, 16, 0)) == date(2026, 10, 24)
    assert cutoff_date(utc(2026, 10, 25, 16, 59)) == date(2026, 10, 24)
    assert cutoff_date(utc(2026, 10, 25, 17, 0)) == date(2026, 10, 25)


# ---------------------------------------------------------------------------
# Pence/pounds switches
# ---------------------------------------------------------------------------

def test_unit_fix_on_real_bcg_data():
    df = load_fixture("bcg_l")
    assert df["Close"].pct_change().abs().max() > 0.9          # the Yahoo glitch is in the fixture
    fixed, n = fix_unit_breaks(df)
    assert n == 1
    assert max_abs_daily_move(fixed) < PRICE_ANOMALY_MOVE      # a real move, no longer a 99% crash
    assert fixed["Close"].iloc[-1] > 100                       # back in pence, like the majority of the window
    assert fixed["Close"].iloc[0] == pytest.approx(df["Close"].iloc[0])   # the longer segment is untouched
    assert (fixed["Volume"] == df["Volume"]).all()


def test_unit_fix_rescales_a_short_pound_segment_to_pence():
    closes = [200.0 + i * 0.1 for i in range(80)] + [2.08 + i * 0.001 for i in range(20)]   # pence, then pounds
    fixed, n = fix_unit_breaks(frame(closes))
    assert n == 1
    assert fixed["Close"].iloc[-1] == pytest.approx(209.9, rel=0.001)
    assert max_abs_daily_move(fixed) < 0.02


def test_unit_fix_rescales_a_short_pence_segment_to_the_dominant_pounds():
    closes = [2.0] * 20 + [200.0] * 5 + [2.0] * 75
    fixed, n = fix_unit_breaks(frame(closes))
    assert n == 2                                               # up, then back down
    assert fixed["Close"].max() == pytest.approx(2.0)


def test_unit_fix_corrects_a_one_bar_spike():
    closes = [150.0] * 50 + [15000.0] + [150.0] * 50
    fixed, n = fix_unit_breaks(frame(closes))
    assert n == 2
    assert fixed["Close"].max() == pytest.approx(150.0)


def test_unit_fix_leaves_clean_data_and_real_jumps_alone():
    df = load_fixture("ror_l")                                  # +68% in one day: a real jump, not a unit switch
    fixed, n = fix_unit_breaks(df)
    assert n == 0
    assert fixed is df
    pd.testing.assert_frame_equal(fixed, df)


def test_unit_fix_applies_to_open_high_low_and_close_but_not_volume():
    closes = [100.0] * 60 + [1.0] * 10
    fixed, _ = fix_unit_breaks(frame(closes, volume=12345))
    tail = fixed.iloc[-1]
    assert tail["Open"] == pytest.approx(100.0) and tail["High"] == pytest.approx(101.0)
    assert tail["Low"] == pytest.approx(99.0) and tail["Volume"] == 12345


# ---------------------------------------------------------------------------
# Price Anomaly
# ---------------------------------------------------------------------------

def clean_one(df, cutoff=date(2030, 1, 1), ticker="X.L"):
    return clean_prices({ticker: df}, cutoff, [ticker])


def test_price_anomaly_threshold():
    just_under = frame([100.0] * 69 + [139.0] + [139.0] * 5)    # +39%
    just_over = frame([100.0] * 69 + [141.0] + [141.0] * 5)     # +41%
    assert "X.L" not in clean_one(just_under).price_anomalies
    assert clean_one(just_over).price_anomalies["X.L"] == pytest.approx(0.41)


def test_real_rotork_jump_is_flagged_but_kept_in_the_data():
    result = clean_one(load_fixture("ror_l"), ticker="ROR.L")
    assert result.price_anomalies["ROR.L"] > 0.6
    assert "ROR.L" in result.data


def test_real_bcg_glitch_is_fixed_not_flagged():
    result = clean_one(load_fixture("bcg_l"), ticker="BCG.L")
    assert result.unit_fixed == {"BCG.L": 1}
    assert "BCG.L" not in result.price_anomalies
    assert result.data["BCG.L"]["Close"].iloc[-1] > 100


# ---------------------------------------------------------------------------
# clean_prices: cutoff, missing data, report lists
# ---------------------------------------------------------------------------

def test_bars_after_the_cutoff_are_removed():
    df = frame([100.0 + i for i in range(100)], start="2026-05-01")
    cutoff = df.index[80].date()
    out = clean_one(df, cutoff).data["X.L"]
    assert out.index[-1].date() == cutoff
    assert len(out) == 81


def test_an_unfinished_todays_bar_is_dropped_for_a_midday_run():
    df = frame([100.0 + i for i in range(100)], start="2026-05-01")
    monday_10am = datetime.combine(df.index[-1].date(), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=9)
    cutoff = cutoff_date(monday_10am)
    assert cutoff < df.index[-1].date()
    assert clean_one(df, cutoff).data["X.L"].index[-1].date() < df.index[-1].date()


def test_rows_without_a_close_are_dropped():
    df = frame([100.0] * 100, start="2026-05-01")
    df.iloc[10, df.columns.get_loc("Close")] = np.nan
    df.iloc[10, df.columns.get_loc("Volume")] = np.nan
    out = clean_one(df).data["X.L"]
    assert len(out) == 99
    assert out["Volume"].dtype == np.int64


def test_missing_empty_malformed_and_short_tickers_are_reported():
    good = frame([100.0] * 100, start="2026-05-01")
    raw = {
        "GOOD.L": good,
        "EMPTY.L": pd.DataFrame(),
        "BAD.L": good.drop(columns=["Volume"]),
        "SHORT.L": good.iloc[: MIN_BARS - 1],
    }
    result = clean_prices(raw, date(2030, 1, 1), ["GOOD.L", "EMPTY.L", "BAD.L", "SHORT.L", "ABSENT.L"])
    assert sorted(result.data) == ["GOOD.L"]
    assert sorted(result.no_data) == ["ABSENT.L", "BAD.L", "EMPTY.L"]
    assert result.short_history == {"SHORT.L": MIN_BARS - 1}
    assert result.coverage == pytest.approx(0.2)


def test_stale_tickers_are_reported_but_kept():
    df = frame([100.0] * 100, start="2026-05-01")
    cutoff = df.index[-1].date() + timedelta(days=10)
    result = clean_one(df, cutoff)
    assert result.stale == {"X.L": df.index[-1].date()}
    assert "X.L" in result.data


def test_a_bar_one_trading_day_behind_is_not_stale():
    df = frame([100.0] * 100, start="2026-05-01")
    cutoff = df.index[-1].date() + timedelta(days=1)
    assert clean_one(df, cutoff).stale == {}


def test_timezone_aware_and_multiindex_frames_are_accepted():
    df = frame([100.0] * 100, start="2026-05-01")
    aware = df.copy()
    aware.index = aware.index.tz_localize("Europe/London")
    multi = df.copy()
    multi.columns = pd.MultiIndex.from_product([["X.L"], df.columns])
    assert len(clean_one(aware).data["X.L"]) == 100
    assert len(clean_one(multi).data["X.L"]) == 100


# ---------------------------------------------------------------------------
# fetch_universe_prices with an injected downloader
# ---------------------------------------------------------------------------

def test_fetch_asks_for_data_up_to_the_cutoff_and_cleans_it():
    seen = {}

    def downloader(tickers, start, end):
        seen.update(tickers=tickers, start=start, end=end)
        return {"A.L": frame([100.0] * 120, start="2026-05-01")}

    as_of = utc(2026, 10, 5, 9, 0)                                # Monday morning, session still open
    result = fetch_universe_prices(as_of, tickers=["A.L", "B.L"], lookback_days=180, downloader=downloader)
    assert seen["tickers"] == ["A.L", "B.L"]
    assert seen["end"] == date(2026, 10, 5)                       # exclusive: bars through Sunday 4 Oct
    assert seen["start"] == date(2026, 10, 4) - timedelta(days=180)
    assert result.cutoff_date == date(2026, 10, 4)
    assert result.no_data == ["B.L"] and list(result.data) == ["A.L"]


def test_fetch_defaults_to_the_whole_universe():
    seen = {}

    def downloader(tickers, start, end):
        seen["n"] = len(tickers)
        return {}

    result = fetch_universe_prices(utc(2026, 10, 4, 6, 0), downloader=downloader)
    assert seen["n"] == len(universe_tickers()) == 853
    assert result.coverage == 0.0 and len(result.no_data) == 853
