import io
import json
import sys
import zipfile
import zlib
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stock_selection_lambda"))

import build_zip  # noqa: E402
import email_report  # noqa: E402
import handler  # noqa: E402
from lse_stock_analysis.prices import PriceFetchResult, clean_prices, cutoff_date  # noqa: E402
from lse_stock_analysis.selection import RISK_METRICS, TREND_METRICS, SectorSelection  # noqa: E402
from lse_stock_analysis.universe import load_sector_map, universe_tickers  # noqa: E402

SIGNAL_TS = datetime(2026, 10, 4, 6, 0, 3, tzinfo=timezone.utc)      # a Sunday, like the scout's run
KEY = "successful/2026-W40/response_20261004T060000Z.json"
SECTOR_MAP = load_sector_map()
HOUSE = "Housebuilders"


# ---------------------------------------------------------------------------
# Fakes and builders
# ---------------------------------------------------------------------------

class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail_put_for: tuple = ()

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        if any(Key.startswith(p) for p in self.fail_put_for):
            raise OSError("S3 down")
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.encode()


class FakeSES:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_email(self, **kwargs):
        if self.fail:
            raise RuntimeError("SES down")
        self.sent.append(kwargs)


class Context:
    def __init__(self, ms):
        self.ms = ms

    def get_remaining_time_in_millis(self):
        return self.ms


def payload(signals=None, **kw):
    sigs = signals if signals is not None else [
        {"sector": HOUSE, "confidence": 0.7, "convergence_type": "News-Led", "timestamp": SIGNAL_TS.isoformat()},
        {"sector": "Quantum Computing", "confidence": 0.75, "convergence_type": "News-Led", "timestamp": SIGNAL_TS.isoformat()},
        {"sector": "Water", "confidence": 0.6, "convergence_type": "Divergent", "timestamp": SIGNAL_TS.isoformat()},
    ]
    return {"timestamp": "20261004T060000Z", "success": True, "signals": sigs, "macro": "m", **kw}


def event(key=KEY):
    return {"Records": [{"s3": {"bucket": {"name": "bkt"}, "object": {"key": key}}}]}


def synthetic_raw(tickers):
    """Seeded random-walk OHLCV for each ticker, ending Friday 2026-10-02."""
    index = pd.bdate_range(end="2026-10-02", periods=130)
    raw = {}
    for t in tickers:
        rng = np.random.default_rng(zlib.crc32(t.encode()))
        close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.015, len(index))))
        raw[t] = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close,
                               "Volume": rng.integers(100_000, 2_000_000, len(index))}, index=index)
    return raw


def make_fetch(coverage_tickers=None, calls=None):
    tickers = universe_tickers()
    raw = synthetic_raw(coverage_tickers if coverage_tickers is not None else tickers)

    def fetch(ts):
        if calls is not None:
            calls.append(ts)
        return clean_prices(raw, cutoff_date(ts), tickers)
    return fetch


def good_analysis(data):
    """Every stock analysed as an A-grade momentum setup, risk score by ticker order (deterministic)."""
    out = {}
    for i, t in enumerate(sorted(data)):
        tr = {k: 1.0 for k in TREND_METRICS}
        tr.update(trend="uptrend", rsi=55.0, atr_pct=3.0, swing_setup="momentum", price_vs_bb="inside", volume_surge=False)
        rk = {k: 1.0 for k in RISK_METRICS}
        rk.update(risk_score=1 + i % 9, risk_level="low", setup_quality="A", tradeable=True, atr_stop_pct=4.5,
                  max_position_size_pct=10.0, vol_signal="medium", rsi_signal="neutral", momentum_signal="strong",
                  swing_setup="momentum", trend_signal="uptrend", status="ok")
        out[t] = {"trend": tr, "risk": rk}
    return out


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SES_SENDER", "from@example.com")
    monkeypatch.setenv("SES_RECIPIENT", "to@example.com")


def run(s3=None, ses=None, fetch=None, ev=None, context=None, sleeps=None, scout=None):
    s3 = s3 if s3 is not None else FakeS3()
    ses = ses if ses is not None else FakeSES()
    if KEY not in s3.objects:
        s3.objects[KEY] = json.dumps(scout if scout is not None else payload()).encode()
    out = handler.run(ev or event(), context, s3, ses, fetch=fetch or make_fetch(),
                      sleep=(sleeps.append if sleeps is not None else lambda s: None),
                      now=lambda: datetime(2026, 10, 4, 6, 1, tzinfo=timezone.utc))
    return out["processed"], s3, ses


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_success_writes_snapshot_and_sends_one_email(monkeypatch):
    monkeypatch.setattr(handler, "analyse_universe", good_analysis)
    results, s3, ses = run()
    assert results == [{"status": "ok", "week": "2026-W40", "source_key": KEY,
                        "snapshot_key": "snapshots/2026-W40/universe.csv", "picks": results[0]["picks"]}]
    assert results[0]["picks"] == 2 + 1                          # Housebuilders 0.70 -> 2; Water Divergent -> 1; AI none
    snap = pd.read_csv(io.BytesIO(s3.objects["snapshots/2026-W40/universe.csv"]))
    assert len(snap) == len(universe_tickers()) and snap["is_pick"].sum() == 3 and set(snap["week"]) == {"2026-W40"}
    assert set(snap["price_cutoff"]) == {"2026-10-03"}
    assert len(ses.sent) == 1
    mail = ses.sent[0]
    assert mail["Source"] == "from@example.com" and mail["Destination"] == {"ToAddresses": ["to@example.com"]}
    assert mail["Message"]["Subject"]["Data"] == "LSE Sector Scout — 3 signals · 3 picks · 2026-W40"
    html_body, text_body = mail["Message"]["Body"]["Html"]["Data"], mail["Message"]["Body"]["Text"]["Data"]
    for needle in (HOUSE, "Quantum Computing", "Water", "MACRO REGIME", "STOCK PICKS"):
        assert needle in html_body
    for needle in (HOUSE, "Quantum Computing", "Water", "Divergent", "MACRO REGIME", "PICK "):
        assert needle in text_body
    assert f"s3://bkt/{KEY}" in html_body                              # where the full data is
    assert "no stock in the Sector Map has this as its Primary Sector" in text_body       # Quantum Computing
    assert not any(k.startswith("failed/") for k in s3.objects)


def test_real_agents_smoke_over_synthetic_prices():
    results, s3, ses = run()
    assert results[0]["status"] == "ok"
    assert len(pd.read_csv(io.BytesIO(s3.objects["snapshots/2026-W40/universe.csv"]))) == len(universe_tickers())
    assert len(ses.sent) == 1


def test_non_scout_keys_are_ignored():
    for key in ["failed/2026-W40/attempt_x.json", "snapshots/2026-W40/universe.csv", "outcomes/2026-W40/stubs_x.json",
                "successful/2026-W40/", "successful/notaweek/response_x.json"]:
        results, s3, ses = run(ev=event(key))
        assert results == [] and ses.sent == []


def test_url_encoded_key_is_decoded():
    s3 = FakeS3()
    s3.objects[KEY] = json.dumps(payload()).encode()
    results, _, _ = run(s3=s3, ev=event(KEY.replace("/", "%2F")))
    assert results[0]["status"] == "ok"


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

def failure_checks(results, s3, ses, reason_part, report=True):
    assert results[0]["status"] == "failed" and reason_part in results[0]["error"]
    failed = [k for k in s3.objects if k.startswith("failed/2026-W40/stock_selection_")]
    assert len(failed) == 1 and results[0]["failed_key"] == failed[0]
    record = json.loads(s3.objects[failed[0]])
    assert record["source_key"] == KEY and reason_part in record["error"]
    assert len(ses.sent) == 1                                                  # exactly one email, whatever went wrong
    subject = ses.sent[0]["Message"]["Subject"]["Data"]
    html_body = ses.sent[0]["Message"]["Body"]["Html"]["Data"]
    if report:                                                                 # the sector report still goes out, without picks
        assert subject == "LSE Sector Scout — 3 signals · stock picks FAILED · 2026-W40"
        assert HOUSE in html_body and "Quantum Computing" in html_body and "MACRO REGIME" in html_body
        assert "STOCK PICKS FAILED" in html_body and reason_part in html_body
        assert "Stock picks unavailable this week" in html_body
    else:                                                                      # nothing to build a sector report from
        assert subject == "LSE Sector Scout FAILED — 2026-W40"
    assert "snapshots/2026-W40/universe.csv" not in s3.objects


def test_download_failure_retries_then_records_and_emails_once():
    calls, sleeps = [], []

    def broken(ts):
        calls.append(ts)
        raise RuntimeError("Yahoo blocked this IP")

    results, s3, ses = run(fetch=broken, sleeps=sleeps)                        # does not raise
    failure_checks(results, s3, ses, "Yahoo blocked this IP")
    assert len(calls) == handler.FETCH_ATTEMPTS == 3 and sleeps == [20, 60]


def test_a_transient_download_failure_is_retried_to_success():
    sleeps, state = [], {"n": 0}
    inner = make_fetch()

    def flaky(ts):
        state["n"] += 1
        if state["n"] == 1:
            raise TimeoutError("timed out")
        return inner(ts)

    results, s3, ses = run(fetch=flaky, sleeps=sleeps)
    assert results[0]["status"] == "ok" and sleeps == [20] and len(ses.sent) == 1
    assert not any(k.startswith("failed/") for k in s3.objects)


def test_thin_coverage_counts_as_a_failed_download():
    some = universe_tickers()[:100]                                             # 29% coverage
    results, s3, ses = run(fetch=make_fetch(coverage_tickers=some), sleeps=[])
    failure_checks(results, s3, ses, f"100 of {len(universe_tickers())} stocks returned prices")


def test_no_time_left_stops_retrying():
    calls, sleeps = [], []

    def broken(ts):
        calls.append(ts)
        raise RuntimeError("down")

    results, s3, ses = run(fetch=broken, context=Context(1000), sleeps=sleeps)
    failure_checks(results, s3, ses, "out of time to retry")
    assert len(calls) == 1 and sleeps == []


def test_scout_result_without_signals_fails_before_downloading():
    calls = []
    results, s3, ses = run(fetch=make_fetch(calls=calls), scout=payload(signals=[]))
    failure_checks(results, s3, ses, "no signals", report=False)
    assert calls == []


def test_unreadable_scout_result_is_a_recorded_failure():
    s3 = FakeS3()
    s3.objects[KEY] = b"not json"
    results, s3, ses = run(s3=s3)
    assert results[0]["status"] == "failed" and len(ses.sent) == 1


def test_snapshot_write_failure_is_a_recorded_failure(monkeypatch):
    monkeypatch.setattr(handler, "analyse_universe", good_analysis)
    s3 = FakeS3()
    s3.fail_put_for = ("snapshots/",)
    results, s3, ses = run(s3=s3)
    failure_checks(results, s3, ses, "S3 down")


def test_a_broken_failure_email_does_not_raise():
    results, s3, ses = run(fetch=lambda ts: (_ for _ in ()).throw(RuntimeError("x")), ses=FakeSES(fail=True), sleeps=[])
    assert results[0]["status"] == "failed" and ses.sent == []


def test_a_broken_failure_record_does_not_raise():
    s3 = FakeS3()
    s3.fail_put_for = ("failed/",)
    results, s3, ses = run(s3=s3, fetch=lambda ts: (_ for _ in ()).throw(RuntimeError("x")), sleeps=[])
    assert results[0]["status"] == "failed" and results[0]["failed_key"] is None and len(ses.sent) == 1


@pytest.mark.parametrize("scout, expected", [
    ({"signals": [{"timestamp": "2026-10-04T06:00:03+00:00"}, {"timestamp": "2026-10-04T06:00:01+00:00"}]},
     datetime(2026, 10, 4, 6, 0, 1, tzinfo=timezone.utc)),                         # earliest signal
    ({"signals": [{"timestamp": "2026-10-04T06:00:03Z"}]}, datetime(2026, 10, 4, 6, 0, 3, tzinfo=timezone.utc)),
    ({"signals": [{"timestamp": "2026-10-04T06:00:03"}]}, datetime(2026, 10, 4, 6, 0, 3, tzinfo=timezone.utc)),
    ({"signals": [{}], "timestamp": "20261004T060000Z"}, datetime(2026, 10, 4, 6, 0, 0, tzinfo=timezone.utc)),
])
def test_signal_time(scout, expected):
    assert handler.signal_time(scout) == expected


def test_signal_time_without_any_timestamp_raises():
    with pytest.raises(handler.StockSelectionError):
        handler.signal_time({"signals": [{}]})


# ---------------------------------------------------------------------------
# Email rendering
# ---------------------------------------------------------------------------

def selection(**kw):
    base = dict(sector=HOUSE, confidence=0.7, convergence_type="News-Led", picks_allowed=2, investable=True,
                candidate_count=6, picks=[], runners_up=[])
    base.update(kw)
    return SectorSelection(**base)


PICK = {"ticker": "PSN.L", "company": "Persimmon", "swing_setup": "momentum", "setup_grade": "B", "risk_score": 3,
        "stop_loss_pct": 4.3, "max_position_pct": 20.0, "close": 1234.5}


def prices_result(**kw):
    p = PriceFetchResult(cutoff_date=date(2026, 10, 2), requested=["A.L", "B.L"])
    p.data = {"A.L": pd.DataFrame()}
    p.last_bar = {"A.L": date(2026, 10, 2)}
    for k, v in kw.items():
        setattr(p, k, v)
    return p


SCOUT_SIGNAL = {
    "sector": HOUSE, "confidence": 0.7, "bear_case_probability": 0.2, "convergence_type": "News-Led", "disruption_type": "Regulatory / Policy Shift",
    "disruption_strength": 0.8, "time_to_impact_weeks": 3, "source_diversity": 0.75, "rationale": "Planning reform <b>speeds</b> approvals",
    "propagation": "Faster consents lift completions", "invalidation_risk": "Gilt yields spike", "convergence_note": "News only",
    "retail_thesis": "No Reddit signal", "key_catalysts": ["Spending review", "BoE cut"], "correlated_sectors": ["Building Materials"],
    "conviction_drivers": ["Policy paper published"],
}


def scout_payload(*signals, macro="Rates falling"):
    return {"signals": list(signals) or [SCOUT_SIGNAL], "macro": macro}


def report(selections, signals=None, prices=None, **kw):
    return email_report.build_report_email("2026-W40", SIGNAL_TS, scout_payload(*(signals or [])), selections,
                                           prices or prices_result(), **kw)


def test_email_shows_the_full_signal_card_with_picks_inside_it():
    sel = selection(picks=[PICK], runners_up=[{"ticker": "BKG.L", "company": "Berkeley", "reason": "no swing setup"}])
    subject, html_body, text_body = report([sel], source_uri="s3://bkt/successful/x.json")
    assert subject == "LSE Sector Scout — 1 signal · 1 pick · 2026-W40"
    for needle in ("MACRO REGIME", "Rates falling", "BULL 70%", "BEAR 20%", "Regulatory / Policy Shift", "3 week(s) to impact",
                   "High source diversity", "Rationale", "Mechanism", "Kill switch", "Spending review | BoE cut", "Building Materials",
                   "Policy paper published", "STOCK PICKS", "PSN.L", "CLOSE BUT NOT PICKED", "no swing setup", "s3://bkt/successful/x.json"):
        assert needle in html_body, needle
    assert html_body.index("Rationale") < html_body.index("STOCK PICKS") < html_body.index("PSN.L")   # picks sit inside the card
    assert "Reddit crowd" not in html_body                                                          # "No Reddit signal" is hidden
    assert "PICK PSN.L (Persimmon): momentum, grade B, risk 3/10, stop-loss 4.3%, max position 20.0%" in text_body
    assert "Rationale: Planning reform" in text_body and "MACRO REGIME: Rates falling" in text_body


def test_email_escapes_html_in_scout_text_and_company_names():
    sel = selection(picks=[{**PICK, "company": "AT&T <script>alert(1)</script>"}])
    _, html_body, text_body = report([sel])
    assert "<script>alert(1)</script>" not in html_body and "AT&amp;T &lt;script&gt;" in html_body
    assert "Planning reform <b>speeds</b>" not in html_body and "Planning reform &lt;b&gt;speeds&lt;/b&gt;" in html_body
    assert "AT&T <script>" in text_body


def test_email_messages_for_each_kind_of_sector_strongest_first():
    signals = [{**SCOUT_SIGNAL, "sector": "Water", "confidence": 0.6}, SCOUT_SIGNAL,
               {**SCOUT_SIGNAL, "sector": "Quantum Computing", "confidence": 0.8}]
    sels = [selection(picks=[PICK], runners_up=[{"ticker": "BKG.L", "company": "Berkeley", "reason": "no swing setup"}]),
            selection(sector="Water", confidence=0.6, picks=[]),
            selection(sector="Quantum Computing", confidence=0.8, investable=False, candidate_count=0)]
    subject, html_body, text_body = report(sels, signals)
    assert subject == "LSE Sector Scout — 3 signals · 1 pick · 2026-W40"
    assert "No eligible stock this week" in html_body and "no swing setup" in html_body
    assert "nothing to pick" in html_body
    assert html_body.index("Quantum Computing") < html_body.index(HOUSE) < html_body.index("Water")   # strongest first
    assert "no eligible stock this week" in text_body


def test_a_sparse_older_signal_still_renders():
    old = {"sector": "Insurance", "confidence": 0.85, "timestamp": "2026-05-04T23:30:06+00:00"}      # from before most fields existed
    subject, html_body, _ = report([selection(sector="Insurance", confidence=0.85)], [old])
    assert "Insurance" in html_body and "BULL 85%" in html_body and "BEAR" not in html_body


def test_email_with_no_signals_and_singular_subject():
    subject, html_body, _ = email_report.build_report_email("2026-W40", SIGNAL_TS, {"signals": []}, [], prices_result())
    assert subject == "LSE Sector Scout — 0 signals · 0 picks · 2026-W40" and "no signals" in html_body
    one, *_ = report([selection(picks=[PICK])])
    assert one == "LSE Sector Scout — 1 signal · 1 pick · 2026-W40"


def test_failed_picks_still_give_the_complete_sector_report():
    subject, html_body, text_body = email_report.build_report_email(
        "2026-W40", SIGNAL_TS, scout_payload(), None, None, error="price download failed: Yahoo blocked this IP",
        failed_key="failed/2026-W40/x.json")
    assert subject == "LSE Sector Scout — 1 signal · stock picks FAILED · 2026-W40"
    for needle in ("MACRO REGIME", "Rationale", "BULL 70%", "STOCK PICKS FAILED", "Yahoo blocked this IP", "failed/2026-W40/x.json",
                   "Stock picks unavailable this week"):
        assert needle in html_body, needle
    assert "STOCK PICKS FAILED: price download failed" in text_body and "Rationale: Planning reform" in text_body
    assert "DATA WARNINGS" not in html_body


def test_a_clean_run_has_no_warnings():
    p = prices_result(requested=["A.L"])
    assert email_report.build_warnings(p, map_age_days=10) == []
    assert "DATA WARNINGS" not in report([selection()], prices=p, map_age_days=10)[1]
    assert "DATA WARNINGS" in report([selection()], prices=prices_result(no_data=["GONE.L"]))[1]


def test_warnings_cover_every_data_problem():
    p = prices_result(no_data=["GONE.L"], short_history={"NEW.L": 40}, stale={"OLD.L": date(2026, 9, 1)},
                      price_anomalies={"ROR.L": 0.67}, unit_fixed={"BCG.L": 1})
    p.last_bar = {f"T{i}.L": date(2026, 10, 1) for i in range(5)}
    warnings = " | ".join(email_report.build_warnings(p, map_age_days=120))
    for needle in ("1 of 2 stocks", "GONE.L", "NEW.L (40 bars)", "OLD.L (last bar 2026-09-01)", "ROR.L (67%)",
                   "BCG.L", "2026-10-01, behind the 2026-10-02 cutoff", "120 days ago"):
        assert needle in warnings, needle


def test_long_warning_lists_are_truncated():
    p = prices_result(no_data=[f"X{i:02d}.L" for i in range(30)])
    warning = next(w for w in email_report.build_warnings(p) if w.startswith("No price data"))
    assert "and 18 more" in warning and "X29.L" not in warning


def test_sector_map_age_is_read_from_the_map():
    age = email_report.sector_map_age_days(date(2026, 10, 12))
    assert age == 10                                                           # map built from the 2026-10-02 constituents


def test_short_failure_email_is_only_for_when_there_is_no_sector_report():
    subject, html_body, text = email_report.build_failure_email("2026-W40", KEY, "boom <b>", "failed/x.json")
    assert subject == "LSE Sector Scout FAILED — 2026-W40"
    assert "boom <b>" in text and "failed/x.json" in text and "boom &lt;b&gt;" in html_body


# ---------------------------------------------------------------------------
# The email goes out even when Yahoo does not cooperate
# ---------------------------------------------------------------------------

def test_a_hanging_download_is_abandoned_in_time_and_the_sector_report_still_goes_out(monkeypatch):
    import threading
    release = threading.Event()
    monkeypatch.setattr(handler, "MIN_ATTEMPT_SECONDS", 0.2)
    monkeypatch.setattr(handler, "RESERVE_SECONDS", 0)

    def hangs(ts):
        release.wait(10)                                                       # a download that never answers
        raise AssertionError("should have been abandoned")

    try:
        results, s3, ses = run(fetch=hangs, context=Context(300), sleeps=[])   # 0.3 s left, so no second attempt either
    finally:
        release.set()
    failure_checks(results, s3, ses, "still running after")
    assert "out of time to retry" in results[0]["error"]


def test_the_attempt_time_limit_is_capped_and_reserves_time_for_the_email():
    assert handler._attempt_seconds(Context(600_000)) == handler.MAX_ATTEMPT_SECONDS
    assert handler._attempt_seconds(Context(150_000)) == 150 - handler.RESERVE_SECONDS
    assert handler._attempt_seconds(Context(1_000)) == handler.MIN_ATTEMPT_SECONDS
    assert handler._attempt_seconds(None) is None                              # no Lambda clock (tests, local runs)


def test_if_the_report_cannot_be_rendered_the_short_failure_email_is_sent(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("template bug")

    monkeypatch.setattr(handler, "build_report_email", broken)
    results, s3, ses = run(fetch=lambda ts: (_ for _ in ()).throw(RuntimeError("Yahoo down")), sleeps=[])
    assert results[0]["status"] == "failed" and len(ses.sent) == 1
    assert ses.sent[0]["Message"]["Subject"]["Data"] == "LSE Sector Scout FAILED — 2026-W40"


def test_a_slow_but_successful_download_is_not_cut_off():
    inner = make_fetch()

    def slow(ts):
        import time
        time.sleep(0.05)
        return inner(ts)

    results, s3, ses = run(fetch=slow, context=Context(600_000))
    assert results[0]["status"] == "ok" and len(ses.sent) == 1


# ---------------------------------------------------------------------------
# Build script
# ---------------------------------------------------------------------------

def test_assemble_lays_out_the_zip_root(tmp_path):
    build_zip.assemble(tmp_path)
    files = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert {"handler.py", "email_report.py", "backtest_handler.py", "backtest_report.py", "lse_stock_analysis/backtest.py",
            "lse_stock_analysis/__init__.py", "lse_stock_analysis/sector_map.json",
            "lse_stock_analysis/prices.py", "lse_stock_analysis/selection.py", "lse_stock_analysis/universe.py",
            "lse_stock_analysis/agents/trend_analysis_agent.py", "lse_stock_analysis/agents/risk_scoring_agent.py"} <= files
    for excluded in ("main.py", "model_loader.py", "get_stock_data.py", "data_cache.csv", "universe_check.py",
                     "agents/return_projection_agent.py"):
        assert f"lse_stock_analysis/{excluded}" not in files
    assert not any("__pycache__" in f or f.endswith(".pyc") for f in files)


def test_prune_removes_tests_bytecode_and_scripts(tmp_path):
    for d in ["pandas/tests/frame", "numpy/_core/tests", "pkg/__pycache__", "bin", "pandas/core"]:
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "f.py").write_text("x")
    build_zip.prune(tmp_path)
    left = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("f.py")}
    assert left == {"pandas/core/f.py"}


def test_write_zip_round_trip(tmp_path):
    build = tmp_path / "b"
    (build / "lse_stock_analysis").mkdir(parents=True)
    (build / "handler.py").write_text("print(1)")
    (build / "lse_stock_analysis" / "sector_map.json").write_text("{}")
    out = tmp_path / "out" / "x.zip"
    build_zip.write_zip(build, out)
    assert sorted(zipfile.ZipFile(out).namelist()) == ["handler.py", "lse_stock_analysis/sector_map.json"]


def test_zip_size_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(build_zip, "directory_mb", lambda p: 245.0)
    monkeypatch.setattr(build_zip, "install_dependencies", lambda t: None)
    with pytest.raises(SystemExit, match="too close"):
        build_zip.build(tmp_path / "x.zip")
