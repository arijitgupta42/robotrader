import io
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lse_stock_analysis.prices import PriceFetchResult, clean_prices
from lse_stock_analysis.selection import (
    RISK_METRICS,
    TREND_METRICS,
    analyse_universe,
    build_snapshot,
    picks_allowed,
    select_stocks,
    snapshot_to_csv,
    week_label,
)

FIXTURES = Path(__file__).parent / "fixtures"
HOUSE = "Housebuilders"
CUTOFF = date(2026, 10, 2)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def an(setup="momentum", grade="A", risk=3, tradeable=True, trend="uptrend", rsi_signal="neutral", rsi=55.0, atr=3.0):
    """A fake analysis entry in the shape analyse_universe() returns."""
    t = {k: 1.0 for k in TREND_METRICS}
    t.update(trend=trend, rsi=rsi, atr_pct=atr, swing_setup=setup, price_vs_bb="inside", volume_surge=False)
    r = {k: 1.0 for k in RISK_METRICS}
    r.update(risk_score=risk, risk_level="low", setup_quality=grade, tradeable=tradeable, atr_stop_pct=atr * 1.5,
             max_position_size_pct=10.0, vol_signal="medium", rsi_signal=rsi_signal, momentum_signal="strong",
             swing_setup=setup, trend_signal=trend, status="ok")
    return {"trend": t, "risk": r}


def entry(sector, secondary=(), company=None, index="FTSE 250"):
    return {"company": company or "Co", "index": index, "icb_sector": "x", "primary_sector": sector,
            "secondary_sectors": list(secondary)}


def closes(n=100, last=100.0):
    return pd.DataFrame({"Close": np.linspace(last, last, n)}, index=pd.bdate_range("2026-05-01", periods=n))


def prices_for(tickers, **kw):
    p = PriceFetchResult(cutoff_date=CUTOFF, requested=list(tickers))
    p.data = {t: closes() for t in tickers}
    p.last_bar = {t: CUTOFF for t in tickers}
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def signal(sector=HOUSE, confidence=0.7, convergence="News-Led"):
    return {"sector": sector, "confidence": confidence, "convergence_type": convergence}


# ---------------------------------------------------------------------------
# Picks per sector
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("confidence, convergence, expected", [
    (0.0, "News-Led", 1), (0.55, "News-Led", 1), (0.649, "News-Led", 1),
    (0.65, "News-Led", 2), (0.72, "Convergent", 2), (0.80, "Convergent", 2),
    (0.801, "News-Led", 3), (0.95, "Convergent", 3),
    (0.90, "Divergent", 1), (0.70, "Divergent", 1), (0.90, "Reddit-Led", 1), (0.70, "Reddit-Led", 1),
])
def test_picks_allowed(confidence, convergence, expected):
    assert picks_allowed(confidence, convergence) == expected


# ---------------------------------------------------------------------------
# Selection rules
# ---------------------------------------------------------------------------

def one_sector(analysis, sig=None, **price_kw):
    tickers = list(analysis)
    smap = {t: entry(HOUSE) for t in tickers}
    smap["OTHER.L"] = entry("Water")
    prices = prices_for(tickers, **price_kw)
    return select_stocks([sig or signal()], analysis, prices, smap)[0]


def test_ranking_is_grade_then_risk_then_ticker():
    sel = one_sector({
        "B1.L": an(grade="B", risk=1), "A5.L": an(grade="A", risk=5), "A2.L": an(grade="A", risk=2),
        "A2B.L": an(grade="A", risk=2),
    }, signal(confidence=0.9))                                   # 3 picks
    assert [p["ticker"] for p in sel.picks] == ["A2.L", "A2B.L", "A5.L"]
    assert sel.runners_up[0]["ticker"] == "B1.L"
    assert sel.runners_up[0]["reason"] == "ranked below the pick limit"


def test_pick_count_follows_confidence():
    analysis = {f"S{i}.L": an(risk=i) for i in range(1, 6)}
    assert len(one_sector(analysis, signal(confidence=0.60)).picks) == 1
    assert len(one_sector(analysis, signal(confidence=0.70)).picks) == 2
    assert len(one_sector(analysis, signal(confidence=0.85)).picks) == 3


def test_divergent_and_reddit_led_signals_get_one_pick():
    analysis = {f"S{i}.L": an(risk=i) for i in range(1, 6)}
    assert len(one_sector(analysis, signal(confidence=0.9, convergence="Divergent")).picks) == 1
    assert len(one_sector(analysis, signal(confidence=0.9, convergence="Reddit-Led")).picks) == 1


def test_fewer_eligible_candidates_than_picks_allowed():
    sel = one_sector({"A.L": an(), "C.L": an(grade="C")}, signal(confidence=0.9))
    assert [p["ticker"] for p in sel.picks] == ["A.L"]
    assert sel.picks_allowed == 3 and sel.candidate_count == 2


def test_grade_c_is_not_picked():
    sel = one_sector({"C.L": an(grade="C"), "A.L": an(grade="A", risk=9)}, signal(confidence=0.9))
    assert [p["ticker"] for p in sel.picks] == ["A.L"]
    assert sel.runners_up[0]["reason"] == "setup grade C (below B)"


@pytest.mark.parametrize("kwargs, reason", [
    (dict(setup="none", tradeable=False, grade="none"), "no swing setup"),
    (dict(tradeable=False, trend="downtrend"), "trend is downtrend"),
    (dict(tradeable=False, rsi_signal="overbought", rsi=78.0), "RSI 78 is overbought"),
    (dict(tradeable=False, atr=6.0), "volatility too high (ATR 6.0%)"),
])
def test_not_tradeable_reasons(kwargs, reason):
    sel = one_sector({"X.L": an(**kwargs), "OK.L": an()}, signal(confidence=0.6))
    assert [p["ticker"] for p in sel.picks] == ["OK.L"]
    assert sel.runners_up[0]["ticker"] == "X.L" and sel.runners_up[0]["reason"] == reason


def test_price_anomaly_is_excluded_even_with_a_top_setup():
    sel = one_sector({"BID.L": an(grade="A", risk=1), "OK.L": an(grade="B", risk=5)}, signal(confidence=0.6),
                     price_anomalies={"BID.L": 0.67})
    assert [p["ticker"] for p in sel.picks] == ["OK.L"]
    assert sel.runners_up[0]["reason"] == "Price Anomaly: moved 67% in one day"


def test_stale_no_data_and_short_history_are_excluded():
    analysis = {"STALE.L": an(), "SHORT.L": an(), "OK.L": an(risk=9)}
    smap = {t: entry(HOUSE) for t in [*analysis, "GONE.L"]}
    prices = prices_for(["STALE.L", "OK.L"], stale={"STALE.L": date(2026, 9, 1)}, short_history={"SHORT.L": 40})
    sel = select_stocks([signal(confidence=0.9)], analysis, prices, smap)[0]
    assert [p["ticker"] for p in sel.picks] == ["OK.L"]
    reasons = {r["ticker"]: r["reason"] for r in sel.runners_up}
    assert reasons["STALE.L"].startswith("stale prices")
    assert reasons["SHORT.L"] == "not enough price history (40 bars)"
    assert reasons["GONE.L"] == "no usable price data"


def test_uninvestable_sector_has_no_stocks_and_is_reported():
    smap = {"A.L": entry("Water")}
    sel = select_stocks([signal("AI Infrastructure", 0.8)], {"A.L": an()}, prices_for(["A.L"]), smap)[0]
    assert sel.investable is False and sel.candidate_count == 0
    assert sel.picks == [] and sel.runners_up == []


def test_unmapped_and_secondary_only_stocks_are_never_candidates():
    smap = {"UNMAPPED.L": entry(None), "SECOND.L": entry("Water", secondary=[HOUSE]), "PRIME.L": entry(HOUSE)}
    analysis = {t: an() for t in smap}
    sel = select_stocks([signal(confidence=0.9)], analysis, prices_for(list(smap)), smap)[0]
    assert sel.candidate_count == 1
    assert [p["ticker"] for p in sel.picks] == ["PRIME.L"]


def test_duplicate_sector_signals_use_the_stronger_one():
    smap = {f"S{i}.L": entry(HOUSE) for i in range(4)}
    analysis = {t: an() for t in smap}
    sels = select_stocks([signal(confidence=0.6), signal(confidence=0.9), signal("Water", 0.7)],
                         analysis, prices_for(list(smap)), smap)
    assert [s.sector for s in sels] == [HOUSE, "Water"]
    assert sels[0].confidence == 0.9 and len(sels[0].picks) == 3


def test_runners_up_are_capped_with_eligible_leftovers_first():
    analysis = {f"A{i}.L": an(risk=i) for i in range(1, 5)}
    analysis["BAD.L"] = an(grade="C")
    sel = one_sector(analysis, signal(confidence=0.6))            # 1 pick
    assert len(sel.runners_up) == 3
    assert [r["ticker"] for r in sel.runners_up] == ["A2.L", "A3.L", "A4.L"]


def test_selection_does_not_mutate_inputs():
    analysis = {"A.L": an()}
    before = repr(analysis)
    one_sector(analysis)
    assert repr(analysis) == before


# ---------------------------------------------------------------------------
# Universe Snapshot
# ---------------------------------------------------------------------------

def make_snapshot():
    smap = {
        "PICK.L": entry(HOUSE, secondary=["Water"], company="Pick Co", index="FTSE 100"),
        "CAND.L": entry(HOUSE),
        "BID.L": entry(HOUSE),
        "WATER.L": entry("Water"),
        "TRUST.L": entry(None),
        "GONE.L": entry("Water"),
    }
    analysis = {"PICK.L": an(risk=2), "CAND.L": an(grade="C"), "BID.L": an(risk=1), "WATER.L": an(), "TRUST.L": an()}
    prices = prices_for(list(analysis), price_anomalies={"BID.L": 0.5}, unit_fixed={"WATER.L": 1})
    sigs = [signal(HOUSE, 0.7, "Convergent")]
    sels = select_stocks(sigs, analysis, prices, smap)
    ts = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
    return build_snapshot(ts, sigs, analysis, prices, sels, smap), sels


def test_snapshot_has_one_row_per_universe_stock():
    snap, _ = make_snapshot()
    assert len(snap) == 6 and snap["ticker"].is_unique
    assert list(snap["ticker"]) == sorted(snap["ticker"])


def test_snapshot_columns():
    snap, _ = make_snapshot()
    for col in ["week", "signal_ts", "price_cutoff", "backfilled", "ticker", "company", "index", "primary_sector",
                "secondary_sectors", "status", "last_bar", "close", "price_anomaly", "unit_fixed", "setup_grade",
                "swing_setup", "risk_score", "tradeable", "atr_stop_pct", "max_position_size_pct", "rsi", "macd_histogram",
                "is_candidate", "is_pick", "signal_confidence", "signal_convergence_type"]:
        assert col in snap.columns, col
    assert "setup_quality" not in snap.columns


def test_snapshot_flags_candidates_picks_and_anomalies():
    snap, sels = make_snapshot()
    by = snap.set_index("ticker")
    assert sels[0].picks[0]["ticker"] == "PICK.L"
    assert bool(by.loc["PICK.L", "is_pick"]) and bool(by.loc["PICK.L", "is_candidate"])
    assert bool(by.loc["CAND.L", "is_candidate"]) and not bool(by.loc["CAND.L", "is_pick"])   # grade C
    assert bool(by.loc["BID.L", "is_candidate"]) and bool(by.loc["BID.L", "price_anomaly"]) and not bool(by.loc["BID.L", "is_pick"])
    assert not by.loc[["WATER.L", "TRUST.L", "GONE.L"], "is_candidate"].any()
    assert by.loc["WATER.L", "unit_fixed"] == 1
    assert by.loc["PICK.L", "secondary_sectors"] == "Water"


def test_snapshot_signal_columns_only_for_candidates():
    snap, _ = make_snapshot()
    by = snap.set_index("ticker")
    assert by.loc["PICK.L", "signal_confidence"] == 0.7
    assert by.loc["PICK.L", "signal_convergence_type"] == "Convergent"
    assert pd.isna(by.loc["WATER.L", "signal_confidence"])


def test_snapshot_marks_stocks_without_data():
    snap, _ = make_snapshot()
    by = snap.set_index("ticker")
    assert by.loc["GONE.L", "status"] == "no_data" and pd.isna(by.loc["GONE.L", "close"])
    assert by.loc["PICK.L", "status"] == "ok" and by.loc["PICK.L", "close"] == 100.0


def test_snapshot_week_cutoff_and_backfill_columns():
    snap, _ = make_snapshot()
    assert set(snap["week"]) == {"2026-W40"}
    assert set(snap["price_cutoff"]) == {"2026-10-02"}
    assert not snap["backfilled"].any()


def test_snapshot_csv_round_trip():
    snap, _ = make_snapshot()
    back = pd.read_csv(io.StringIO(snapshot_to_csv(snap)))
    assert len(back) == len(snap) and list(back.columns) == list(snap.columns)
    assert back.set_index("ticker").loc["PICK.L", "is_pick"]


@pytest.mark.parametrize("ts, label", [
    (datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc), "2026-W40"),
    (datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc), "2026-W53"),
    (datetime(2026, 10, 4, 6, 0), "2026-W40"),
])
def test_week_label(ts, label):
    assert week_label(ts) == label


# ---------------------------------------------------------------------------
# With the real agents
# ---------------------------------------------------------------------------

def test_real_agents_over_real_price_fixtures():
    raw = {t: pd.read_csv(FIXTURES / f, index_col=0, parse_dates=True) for t, f in [("BCG.L", "bcg_l.csv"), ("ROR.L", "ror_l.csv")]}
    prices = clean_prices(raw, date(2026, 10, 3))
    analysis = analyse_universe(prices.data)
    assert set(analysis) == {"BCG.L", "ROR.L"}
    for t in analysis:
        assert analysis[t]["risk"]["status"] == "ok"
        assert analysis[t]["risk"]["setup_quality"] in ("A", "B", "C", "none")
    smap = {"BCG.L": entry("UK General Retail"), "ROR.L": entry("Engineering & Industrials")}
    sels = select_stocks([signal("Engineering & Industrials", 0.9), signal("UK General Retail", 0.5)], analysis, prices, smap)
    rotork = next(s for s in sels if s.sector == "Engineering & Industrials")
    assert rotork.picks == [] and rotork.runners_up[0]["reason"].startswith("Price Anomaly")   # the +67% jump
    snap = build_snapshot(datetime(2026, 10, 4, 6, tzinfo=timezone.utc), [signal("UK General Retail", 0.5)],
                          analysis, prices, sels, smap)
    assert len(snap) == 2 and snap.set_index("ticker").loc["ROR.L", "price_anomaly"]


def test_selection_results_are_json_serialisable_even_with_numpy_inputs():
    import json
    a = an(risk=np.int64(2), atr=np.float64(3.0), rsi=np.float64(55.123))
    a["risk"]["max_position_size_pct"] = np.float64(12.34)
    sel = one_sector({"A.L": a, "B.L": an(grade="C")}, signal(confidence=0.6))
    json.dumps(sel.to_dict())                                # would raise on np.int64
    assert isinstance(sel.picks[0]["risk_score"], int) and sel.picks[0]["rsi"] == 55.1
