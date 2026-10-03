import io
import sys
import zlib
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stock_selection_lambda"))

import backfill  # noqa: E402
from lse_stock_analysis.prices import cutoff_date, fetch_universe_prices  # noqa: E402
from lse_stock_analysis.selection import analyse_universe, build_snapshot, select_stocks, snapshot_to_csv  # noqa: E402
from lse_stock_analysis.universe import load_sector_map  # noqa: E402

SECTOR_MAP = load_sector_map()
HOUSE = "Housebuilders"
TICKERS = sorted(t for t, e in SECTOR_MAP.items() if e["primary_sector"] == HOUSE) + sorted(SECTOR_MAP)[:15]
TICKERS = sorted(set(TICKERS))

SAT_AUG = datetime(2026, 8, 1, 6, 12, 5, tzinfo=timezone.utc)
SAT_SEP = datetime(2026, 9, 12, 6, 17, 11, tzinfo=timezone.utc)


def signal(ts, sector=HOUSE, confidence=0.7, **extra):
    return {"sector": sector, "confidence": confidence, "convergence_type": "News-Led",
            "timestamp": ts.isoformat(), **extra}


def result(ts, signals=None, success=True):
    return {"timestamp": ts.strftime("%Y%m%dT%H%M%SZ"), "success": success,
            "signals": signals if signals is not None else [signal(ts)]}


def key(ts, week=None):
    iso = ts.isocalendar()
    return f"successful/{iso[0]}-W{iso[1]:02d}/response_{ts.strftime('%Y%m%dT%H%M%SZ')}.json"


def synthetic_raw(tickers, end="2026-10-02", periods=420):
    index = pd.bdate_range(end=end, periods=periods)
    raw = {}
    for t in tickers:
        rng = np.random.default_rng(zlib.crc32(t.encode()))
        close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.015, len(index))))
        raw[t] = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close,
                               "Volume": rng.integers(100_000, 2_000_000, len(index))}, index=index)
    return raw


class Downloads:
    """A download function that counts its calls and serves the synthetic frames for the span asked."""

    def __init__(self, raw):
        self.raw, self.calls = raw, []

    def __call__(self, tickers, start, end):
        self.calls.append((start, end))
        return {t: df for t, df in self.raw.items() if t in tickers}


# ---------------------------------------------------------------------------
# plan_weeks
# ---------------------------------------------------------------------------

def test_plan_takes_the_first_success_with_signals_in_each_week():
    early = datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc)
    later = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)         # Monday, so already W32
    same_week = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)    # Wednesday of W31
    payloads = {key(same_week): result(same_week, signals=[]),       # success but no signals: skipped
                key(early): result(early),
                key(later): result(later),
                "successful/2026-W35/response_20260829T064437Z.json": result(datetime(2026, 8, 29, 6, 44, tzinfo=timezone.utc), success=False)}
    plans = backfill.plan_weeks(list(payloads) + ["failed/2026-W31/attempt_x.json"], payloads.__getitem__)
    assert [p.week for p in plans] == ["2026-W31", "2026-W32"]
    assert plans[0].source_key == key(early) and plans[0].signal_ts == early
    assert plans[1].source_key == key(later)


def test_plan_groups_by_signal_week_and_skips_unreadable_results():
    ts = SAT_SEP
    payloads = {key(ts): result(ts)}

    def load(k):
        if k not in payloads:
            raise ValueError("corrupt json")
        return payloads[k]
    plans = backfill.plan_weeks([key(ts), "successful/2026-W20/response_20260516T061456Z.json"], load)
    assert [p.week for p in plans] == ["2026-W37"]


def test_results_without_convergence_type_get_unknown_and_keyword_fallback_is_flagged():
    ts = datetime(2026, 5, 4, 23, 30, tzinfo=timezone.utc)
    old = {"sector": "Integrated Oil & Gas", "confidence": 0.85, "timestamp": ts.isoformat(),
           "propagation": "Keyword signal: 7 sector matches (heuristic fallback)."}
    plans = backfill.plan_weeks([key(ts)], lambda k: result(ts, signals=[old]))
    assert plans[0].signals[0]["convergence_type"] == backfill.UNKNOWN_CONVERGENCE
    assert plans[0].keyword_fallback
    assert "convergence_type" not in old                             # the input is not mutated


def test_llm_signals_are_not_flagged_as_keyword_fallback():
    plans = backfill.plan_weeks([key(SAT_SEP)], lambda k: result(SAT_SEP))
    assert not plans[0].keyword_fallback


# ---------------------------------------------------------------------------
# One download, per-week slices
# ---------------------------------------------------------------------------

def test_download_span_covers_the_lookback_before_the_earliest_week():
    plans = [backfill.WeekPlan("a", "k", SAT_AUG, [], False), backfill.WeekPlan("b", "k", SAT_SEP, [], False)]
    start, end = backfill.download_span(plans)
    assert (cutoff_date(SAT_AUG) - start).days == 180
    assert end == date(2026, 9, 12)                                   # cutoff Friday 11 Sep, exclusive end the day after


def test_slicing_downloader_downloads_once_and_slices_by_date():
    raw = synthetic_raw(TICKERS[:3])
    dl = Downloads(raw)
    sliced = backfill.SlicingDownloader(date(2025, 1, 1), date(2026, 10, 3), dl)
    a = sliced(TICKERS[:3], date(2026, 6, 1), date(2026, 7, 1))
    b = sliced(TICKERS[:3], date(2026, 6, 1), date(2026, 8, 1))
    assert len(dl.calls) == 1
    frame = a[TICKERS[0]]
    assert frame.index.min() >= pd.Timestamp("2026-06-01") and frame.index.max() < pd.Timestamp("2026-07-01")
    assert len(b[TICKERS[0]]) > len(frame)


def test_slicing_downloader_handles_timezone_aware_indexes():
    raw = synthetic_raw(TICKERS[:1])
    raw = {t: df.set_axis(df.index.tz_localize("Europe/London")) for t, df in raw.items()}
    sliced = backfill.SlicingDownloader(date(2025, 1, 1), date(2026, 10, 3), Downloads(raw))
    out = sliced(TICKERS[:1], date(2026, 6, 1), date(2026, 7, 1))
    assert out[TICKERS[0]].index.min().date() == date(2026, 6, 1)
    assert out[TICKERS[0]].index.max().date() == date(2026, 6, 30)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

class Store:
    def __init__(self, existing=()):
        self.files = {}
        self.existing = set(existing)

    def write(self, week, text):
        self.files[week] = text

    def exists(self, week):
        return week in self.existing


def make_plans():
    return backfill.plan_weeks([key(SAT_AUG), key(SAT_SEP)], {key(SAT_AUG): result(SAT_AUG), key(SAT_SEP): result(SAT_SEP)}.__getitem__)


def test_backfill_writes_a_backfilled_snapshot_per_week_with_one_download():
    dl, store = Downloads(synthetic_raw(TICKERS)), Store()
    out = backfill.backfill(make_plans(), store.write, store.exists, download=dl, tickers=TICKERS)
    assert out == {"written": ["2026-W31", "2026-W37"], "skipped": [], "failed": []}
    assert len(dl.calls) == 1
    frame = pd.read_csv(io.StringIO(store.files["2026-W37"]))
    assert frame["backfilled"].all()
    assert set(frame["week"]) == {"2026-W37"} and len(frame) == len(SECTOR_MAP)
    assert frame["is_candidate"].any()


def test_backfilled_snapshot_matches_what_the_weekly_lambda_would_have_built():
    raw = synthetic_raw(TICKERS)
    store = Store()
    backfill.backfill(make_plans(), store.write, store.exists, download=Downloads(raw), tickers=TICKERS)

    plan = make_plans()[1]
    def yahoo_like(tickers, start, end):                             # what yfinance does: only bars in [start, end)
        return {t: df[(df.index >= pd.Timestamp(start)) & (df.index < pd.Timestamp(end))] for t, df in raw.items()}
    prices = fetch_universe_prices(plan.signal_ts, tickers=TICKERS, downloader=yahoo_like)           # the weekly path
    analysis = analyse_universe(prices.data)
    live = build_snapshot(plan.signal_ts, plan.signals, analysis, prices, select_stocks(plan.signals, analysis, prices),
                          backfilled=True)
    assert store.files["2026-W37"] == snapshot_to_csv(live)


def test_existing_snapshots_are_skipped_unless_overwriting():
    store = Store(existing={"2026-W31"})
    out = backfill.backfill(make_plans(), store.write, store.exists, download=Downloads(synthetic_raw(TICKERS)), tickers=TICKERS)
    assert out["skipped"] == ["2026-W31"] and out["written"] == ["2026-W37"]
    out = backfill.backfill(make_plans(), store.write, store.exists, overwrite=True,
                            download=Downloads(synthetic_raw(TICKERS)), tickers=TICKERS)
    assert out["written"] == ["2026-W31", "2026-W37"]


def test_nothing_is_downloaded_when_every_week_already_has_a_snapshot():
    dl = Downloads({})
    store = Store(existing={"2026-W31", "2026-W37"})
    out = backfill.backfill(make_plans(), store.write, store.exists, download=dl, tickers=TICKERS)
    assert out["skipped"] == ["2026-W31", "2026-W37"] and dl.calls == []


def test_a_week_with_thin_price_coverage_is_reported_as_failed_not_written():
    thin = synthetic_raw(TICKERS[:3])
    store = Store()
    out = backfill.backfill(make_plans(), store.write, store.exists, download=Downloads(thin), tickers=TICKERS)
    assert out["failed"] == ["2026-W31", "2026-W37"] and store.files == {}


def test_a_week_before_the_price_history_starts_fails_without_stopping_the_others():
    raw = synthetic_raw(TICKERS, periods=40)                         # only ~2 months of bars: both weeks too short
    store = Store()
    out = backfill.backfill(make_plans(), store.write, store.exists, download=Downloads(raw), tickers=TICKERS)
    assert out["written"] == [] and len(out["failed"]) == 2
