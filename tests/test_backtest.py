import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stock_selection_lambda"))

import backtest_report  # noqa: E402
from lse_stock_analysis import backtest  # noqa: E402


def snap(closes, picks=(), cands=(), conf=0.7, conv="News-Led", grade="B", setup="momentum", meth="m1", prompt="p1"):
    """A minimal snapshot: closes {ticker: close}; candidates and picks flagged."""
    rows = []
    for t, c in closes.items():
        is_c = t in cands or t in picks
        rows.append({"ticker": t, "close": c, "is_candidate": is_c, "is_pick": t in picks,
                     "signal_confidence": conf if is_c else None, "signal_convergence_type": conv if is_c else None,
                     "setup_grade": grade, "swing_setup": setup, "primary_sector": "Housebuilders",
                     "methodology_version": meth, "prompt_version": prompt})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label, n, expected", [
    ("2026-W40", 2, "2026-W42"),
    ("2026-W52", 2, "2027-W01"),          # 2026 has 53 ISO weeks: W52 + 2 crosses into 2027
    ("2026-W53", 1, "2027-W01"),
    ("2025-W51", 4, "2026-W03"),
])
def test_week_offset_follows_the_iso_calendar(label, n, expected):
    assert backtest.week_offset(label, n) == expected


@pytest.mark.parametrize("c, band", [(0.6499, "low (<0.65)"), (0.65, "mid (0.65-0.80)"), (0.80, "mid (0.65-0.80)"),
                                      (0.8001, "high (>0.80)"), (None, None), (float("nan"), None)])
def test_confidence_bands_match_the_pick_count_thresholds(c, band):
    assert backtest.confidence_band(c) == band


# ---------------------------------------------------------------------------
# Forward returns
# ---------------------------------------------------------------------------

def test_forward_return_excess_and_hit_are_measured_against_the_universe_mean():
    snaps = {"2026-W10": snap({"A": 100, "B": 100, "C": 100}, picks=("A",), cands=("A", "B")),
             "2026-W12": snap({"A": 110, "B": 90, "C": 100})}
    fr = backtest.forward_returns(snaps)
    assert set(fr["horizon"]) == {2}                                           # no snapshot 4 or 6 weeks on
    by = fr.set_index("ticker")
    assert by.loc["A", "ret"] == pytest.approx(0.10) and by.loc["B", "ret"] == pytest.approx(-0.10)
    assert fr["universe_mean"].iloc[0] == pytest.approx(0.0)
    assert by.loc["A", "excess"] == pytest.approx(0.10) and bool(by.loc["A", "hit"]) is True
    assert bool(by.loc["B", "hit"]) is False and bool(by.loc["C", "hit"]) is False        # C ties the mean: not a hit
    assert bool(by.loc["A", "is_pick"]) and bool(by.loc["B", "is_candidate"]) and not bool(by.loc["C", "is_candidate"])
    assert by.loc["A", "confidence_band"] == "mid (0.65-0.80)"


def test_stocks_missing_or_unpriced_in_either_snapshot_are_dropped():
    first = snap({"A": 100, "B": 100, "C": 100, "D": 100})
    later = snap({"A": 110, "B": 90, "C": np.nan, "E": 50})                     # D gone, C unpriced, E new
    fr = backtest.forward_returns({"2026-W10": first, "2026-W12": later})
    assert sorted(fr["ticker"]) == ["A", "B"]


def test_several_horizons_use_the_right_later_snapshot():
    snaps = {"2026-W10": snap({"A": 100, "B": 100}), "2026-W12": snap({"A": 101, "B": 100}),
             "2026-W14": snap({"A": 104, "B": 100}), "2026-W16": snap({"A": 108, "B": 100})}
    fr = backtest.forward_returns(snaps)
    a = fr[(fr["ticker"] == "A") & (fr["week"] == "2026-W10")].set_index("horizon")["ret"]
    assert a[2] == pytest.approx(0.01) and a[4] == pytest.approx(0.04) and a[6] == pytest.approx(0.08)


def test_no_later_snapshots_gives_an_empty_frame():
    fr = backtest.forward_returns({"2026-W10": snap({"A": 100})})
    assert fr.empty and "excess" in fr.columns


# ---------------------------------------------------------------------------
# Summary and report
# ---------------------------------------------------------------------------

def test_summarise_flags_small_samples_as_indicative():
    fr = backtest.forward_returns({"2026-W10": snap({"A": 100, "B": 100}, cands=("A",)),
                                   "2026-W12": snap({"A": 120, "B": 100})})
    s = backtest.summarise(fr)
    assert s["n"] == 2 and s["weeks"] == 1 and s["mean_ret"] == pytest.approx(0.10) and s["indicative"]
    assert np.isnan(s["excess_se"])                                              # one week: no spread to estimate
    big = pd.DataFrame({"week": [f"w{i % 10}" for i in range(60)], "ret": 0.01, "excess": 0.005, "hit": True})
    assert not backtest.summarise(big)["indicative"]


def make_history(weeks=12, picks_in=(3,)):
    """Consecutive weeks: A rises 1%/week, B falls 1%/week, C flat; A is always a Candidate, a Pick in some weeks."""
    snaps = {}
    label = "2026-W10"
    for i in range(weeks):
        closes = {"A": 100 * 1.01 ** i, "B": 100 * 0.99 ** i, "C": 100.0}
        snaps[label] = snap(closes, picks=("A",) if i in picks_in else (), cands=("A", "B"), prompt="p1" if i < 6 else "p2")
        label = backtest.week_offset(label, 1)
    return snaps


def test_report_compares_picks_candidates_and_universe():
    report = backtest.build_report(make_history())
    h4 = {g: report["headline"][g][4] for g in backtest.GROUPS}
    assert h4["Picks"]["n"] == 1 and h4["Candidates"]["n"] == 2 * h4["Candidates"]["weeks"]
    assert h4["Picks"]["hit_rate"] == 1.0                                       # A beats the (A, B, C) mean
    assert h4["Candidates"]["hit_rate"] == pytest.approx(0.5)                   # A hits, B does not
    assert h4["Universe"]["n"] == 3 * h4["Universe"]["weeks"]
    assert report["pick_count"]["2026-W13"] == 1 and sum(report["pick_count"].values()) == 1


def test_report_breaks_candidates_down_by_version_so_changes_can_be_compared():
    report = backtest.build_report(make_history())
    prompts = report["breakdowns"]["prompt_version"]
    assert set(prompts) == {"p1", "p2"} and prompts["p1"][2]["n"] > 0 and prompts["p2"][2]["n"] > 0
    assert set(report["breakdowns"]["confidence_band"]) == {"mid (0.65-0.80)"}
    assert "methodology_version" in report["breakdowns"]


def test_keyword_fallback_weeks_are_excluded_automatically():
    snaps = make_history()
    snaps["2026-W09"] = snap({"A": 100, "B": 100, "C": 100}, cands=("A",), conv="Unknown")
    report = backtest.build_report(dict(sorted(snaps.items())))
    assert "2026-W09" in report["weeks_excluded"] and "2026-W09" not in report["weeks_used"]


def test_report_renders_even_with_nothing_to_measure():
    report = backtest.build_report({"2026-W10": snap({"A": 100}, cands=("A",))})
    text, html_body = backtest.render_text(report), backtest.render_html(report)
    assert "Picks vs Candidates vs Universe" in text and "n/a" in text
    assert "<table" in html_body


def test_rendered_report_shows_sample_sizes_and_escapes_html():
    report = backtest.build_report(make_history())
    text = backtest.render_text(report)
    assert "(indicative)" in text and "stock-weeks" in text and "Picks per week" in text
    report["weeks_excluded"]["<b>x</b>"] = "<script>"
    html_body = backtest.render_html(report)
    assert "<script>" not in html_body and "&lt;script&gt;" in html_body


# ---------------------------------------------------------------------------
# S3 loader
# ---------------------------------------------------------------------------

class FakeS3:
    def __init__(self, objects):
        self.objects = objects

    def get_paginator(self, name):
        objs = self.objects

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in sorted(objs) if k.startswith(Prefix)]}
        return P()

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}


def test_loader_reads_only_snapshot_csvs_keyed_by_week():
    csv = snap({"A": 100}).to_csv(index=False).encode()
    s3 = FakeS3({"snapshots/2026-W11/universe.csv": csv, "snapshots/2026-W10/universe.csv": csv,
                 "snapshots/notes.txt": b"x", "snapshots/2026-W10/other.csv": b"x", "successful/2026-W10/response_x.json": b"{}"})
    loaded = backtest_report.load_snapshots(s3, "bkt")
    assert list(loaded) == ["2026-W10", "2026-W11"] and list(loaded["2026-W10"]["ticker"]) == ["A"]


# ---------------------------------------------------------------------------
# Two markets: hits are measured against each stock's own market
# ---------------------------------------------------------------------------

def test_a_hit_beats_the_average_of_the_stocks_own_market_not_the_whole_universe():
    first = snap({"U1": 100, "U2": 100, "L1.L": 100, "L2.L": 100}, cands=("U1", "U2", "L1.L", "L2.L"))
    later = snap({"U1": 112, "U2": 108, "L1.L": 101, "L2.L": 99})          # US mean +10%, LSE mean 0%, combined mean +5%
    fr = backtest.forward_returns({"2026-W10": first, "2026-W12": later}).set_index("ticker")
    assert set(fr["market"]) == {"US", "LSE"}
    assert fr.loc["U1", "universe_mean"] == pytest.approx(0.10) and fr.loc["L1.L", "universe_mean"] == pytest.approx(0.0)
    assert [bool(fr.loc[t, "hit"]) for t in ("U1", "U2", "L1.L", "L2.L")] == [True, False, True, False]
    # against the combined +5% benchmark U2 (+8%) would have been a hit and L1.L (+1%) would not


def test_snapshots_from_before_the_sp500_still_count_as_lse():
    legacy = snap({"BP.L": 100, "SHEL.L": 100})
    assert "market" not in legacy.columns
    fr = backtest.forward_returns({"2026-W10": legacy, "2026-W12": snap({"BP.L": 110, "SHEL.L": 90})})
    assert set(fr["market"]) == {"LSE"}


def test_report_has_per_market_tables_and_a_market_breakdown():
    snaps = {}
    label = "2026-W10"
    for i in range(6):
        snaps[label] = snap({"U1": 100 * 1.02 ** i, "U2": 100, "L1.L": 100 * 0.99 ** i, "L2.L": 100}, picks=("U1",),
                            cands=("U1", "L1.L"))
        label = backtest.week_offset(label, 1)
    report = backtest.build_report(snaps)
    assert set(report["by_market"]) == {"LSE", "US"}
    us2 = report["by_market"]["US"]["Candidates"][2]
    lse2 = report["by_market"]["LSE"]["Candidates"][2]
    assert us2["n"] > 0 and lse2["n"] > 0 and us2["hit_rate"] == 1.0 and lse2["hit_rate"] == 0.0     # U1 rises, L1.L falls
    assert set(report["breakdowns"]["market"]) == {"LSE", "US"}
    text = backtest.render_text(report)
    assert "2-week Forward Return, LSE only" in text and "2-week Forward Return, US only" in text
    assert "Candidates by market" in text and "own market" in " ".join(report["notes"])
