import io
import json
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

import handler  # noqa: E402
import portfolio_step as ps  # noqa: E402
from lse_stock_analysis.prices import clean_prices, cutoff_date  # noqa: E402
from lse_stock_analysis.selection import RISK_METRICS, TREND_METRICS  # noqa: E402
from lse_stock_analysis.universe import universe_tickers  # noqa: E402

NOW = datetime(2026, 10, 31, 7, 0, tzinfo=timezone.utc)
WATER = "Water"


# ---------------------------------------------------------------------------
# Fakes and builders
# ---------------------------------------------------------------------------

class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail_put_for: tuple = ()

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}                # KeyError when missing, like NoSuchKey

    def put_object(self, Bucket, Key, Body, ContentType=None):
        if any(Key.startswith(p) for p in self.fail_put_for):
            raise OSError("S3 down")
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.encode()

    def get_paginator(self, name):
        objects = self.objects

        class Pages:
            def paginate(self, Bucket, Prefix):
                return [{"Contents": [{"Key": k} for k in sorted(objects) if k.startswith(Prefix)]}]
        return Pages()

    def json(self, key):
        return json.loads(self.objects[key])


class FakeSES:
    def __init__(self):
        self.sent = []

    def send_email(self, **kwargs):
        self.sent.append(kwargs)


def row(ticker, close, sector=WATER, market="LSE", pick=False, candidate=None, conf=0.8, maxpos=10.0, atr=6.0,
        stale=False, cutoff="2026-10-30"):
    candidate = pick if candidate is None else candidate
    return {
        "ticker": ticker, "market": market, "currency": "USD" if market == "US" else "GBp", "company": f"{ticker} plc",
        "primary_sector": sector, "status": "ok", "close": close, "stale": stale, "price_anomaly": False, "unit_fixed": 0,
        "price_cutoff": cutoff, "is_candidate": candidate, "is_pick": pick,
        "signal_confidence": conf if candidate else None, "signal_convergence_type": "Convergent" if candidate else None,
        "setup_grade": "A", "risk_score": 3, "max_position_size_pct": maxpos, "atr_stop_pct": atr,
        "swing_setup": "momentum", "tradeable": True,
    }


def snap(*rows):
    return pd.DataFrame(list(rows))


def store_snapshot(s3, week, frame):
    s3.objects[f"snapshots/{week}/universe.csv"] = frame.to_csv(index=False).encode()


def fx(rate=1.25, calls=None):
    def fetch(on):
        if calls is not None:
            calls.append(on)
        return rate
    return fetch


W44 = snap(row("AAA", 200.0, pick=True), row("BBB", 100.0, sector="Banks"), row("UUU", 125.0, market="US", sector="Banks"))


def run_week(store, week, frame, rate=1.25, **kw):
    return ps.run_week(store, week, frame, rate, fetch_fx=kw.pop("fetch_fx", fx()), now=lambda: NOW, **kw)


# ---------------------------------------------------------------------------
# Snapshot -> inputs
# ---------------------------------------------------------------------------

def test_week_inputs_come_from_the_snapshot_and_survive_a_csv_round_trip():
    inputs = ps.week_inputs("2026-W44", W44, 1.25)
    assert inputs.scout_ok and inputs.gbpusd == 1.25
    assert [p.ticker for p in inputs.picks] == ["AAA"]
    pick = inputs.picks[0]
    assert (pick.sector, pick.confidence, pick.max_position_size_pct, pick.atr_stop_pct) == (WATER, 0.8, 10.0, 6.0)
    assert inputs.signals == [{"sector": WATER, "confidence": 0.8, "convergence_type": "Convergent"}]
    assert inputs.quotes["UUU"].market == "US" and inputs.quotes["UUU"].close == 125.0
    from_csv = ps.week_inputs("2026-W44", pd.read_csv(io.StringIO(W44.to_csv(index=False))), 1.25)
    assert from_csv == inputs


def test_no_snapshot_is_a_no_trade_week():
    inputs = ps.week_inputs("2026-W45", None, None, scout_error="scout failed")
    assert not inputs.scout_ok and inputs.scout_error == "scout failed" and inputs.quotes == {}


def test_fx_date_is_the_us_cutoff():
    frame = snap(row("AAA", 1.0, cutoff="2026-10-30"), row("UUU", 1.0, market="US", cutoff="2026-10-29"))
    assert ps.fx_date(frame) == date(2026, 10, 29)
    assert ps.fx_date(snap(row("AAA", 1.0))) == date(2026, 10, 30)


def test_fetch_gbpusd_takes_the_last_close_on_or_before_the_day():
    index = pd.to_datetime(["2026-10-28", "2026-10-29", "2026-10-30"])
    frame = pd.DataFrame({"Close": [1.30, 1.31, 1.40]}, index=index)
    assert ps.fetch_gbpusd(date(2026, 10, 29), downloader=lambda t, s, e: {"GBPUSD=X": frame}) == 1.31
    assert ps.fetch_gbpusd(date(2026, 10, 29), downloader=lambda t, s, e: {}) is None
    bad = pd.DataFrame({"Close": [131.0]}, index=index[:1])
    assert ps.fetch_gbpusd(date(2026, 10, 29), downloader=lambda t, s, e: {"GBPUSD=X": bad}) is None

    def boom(*a):
        raise RuntimeError("Yahoo down")
    assert ps.fetch_gbpusd(date(2026, 10, 29), downloader=boom) is None


# ---------------------------------------------------------------------------
# The chain of weeks
# ---------------------------------------------------------------------------

def test_weeks_before_the_opening_week_are_ignored():
    s3 = FakeS3()
    assert run_week(ps.Store(s3, "b"), "2026-W43", W44) is None
    assert s3.objects == {}


def test_opening_week_then_next_week():
    s3 = FakeS3()
    store = ps.Store(s3, "b")
    r44 = run_week(store, "2026-W44", W44, source_key="successful/2026-W44/response_x.json")
    assert [t["ticker"] for t in r44["result"]["trades"]] == ["AAA"]
    assert r44["source_key"] == "successful/2026-W44/response_x.json" and r44["gbpusd"] == 1.25
    r45 = run_week(store, "2026-W45", snap(row("AAA", 150.0), row("UUU", 125.0, market="US", sector="Banks")))
    assert [(t["side"], t["reason"]) for t in r45["result"]["trades"]] == [("sell", "stop")]
    assert store.record_weeks() == ["2026-W44", "2026-W45"]


def test_a_week_with_no_earlier_record_is_refused():
    with pytest.raises(ps.PortfolioError, match="starts at 2026-W44"):
        run_week(ps.Store(FakeS3(), "b"), "2026-W45", W44)


def test_missing_weeks_are_filled_from_stored_snapshots_or_as_no_trade_weeks():
    s3 = FakeS3()
    store = ps.Store(s3, "b")
    run_week(store, "2026-W44", W44)
    store_snapshot(s3, "2026-W45", snap(row("AAA", 210.0, pick=True)))
    calls = []
    r47 = run_week(store, "2026-W47", snap(row("AAA", 220.0, pick=True)), fetch_fx=fx(1.3, calls))
    assert store.record_weeks() == ["2026-W44", "2026-W45", "2026-W46", "2026-W47"]
    assert store.record("2026-W45")["scout_ok"] and store.record("2026-W45")["gbpusd"] == 1.3
    w46 = store.record("2026-W46")
    assert not w46["scout_ok"] and w46["result"]["notes"][0]["kind"] == "no_trade_week"
    assert calls == [date(2026, 10, 30)]                             # only W45's rate had to be fetched
    assert r47["result"]["state"]["positions"][0]["clock_start_week"] == "2026-W47"


def test_rerunning_an_earlier_week_rebuilds_the_weeks_after_it():
    s3 = FakeS3()
    store = ps.Store(s3, "b")
    later = snap(row("AAA", 200.0, candidate=True), row("CCC", 100.0, sector="Banks", pick=True))
    store_snapshot(s3, "2026-W45", later)
    run_week(store, "2026-W44", W44)
    run_week(store, "2026-W45", later)
    assert {p["ticker"] for p in store.record("2026-W45")["result"]["state"]["positions"]} == {"AAA", "CCC"}

    calls = []
    run_week(store, "2026-W44", snap(row("AAA", 200.0), row("BBB", 100.0, sector="Banks")), fetch_fx=fx(9.9, calls))
    w45 = store.record("2026-W45")
    assert {p["ticker"] for p in w45["result"]["state"]["positions"]} == {"CCC"}     # AAA was never bought
    assert w45["gbpusd"] == 1.25 and calls == []                    # the stored rate is reused


def test_rerunning_a_week_gives_the_same_result():
    s3 = FakeS3()
    store = ps.Store(s3, "b")
    first = run_week(store, "2026-W44", W44)
    again = run_week(store, "2026-W44", W44)
    assert first["result"] == again["result"]
    assert store.record_weeks() == ["2026-W44"]


# ---------------------------------------------------------------------------
# Website data
# ---------------------------------------------------------------------------

SCOUT = {
    "timestamp": "20261031T060000Z", "prompt_version": "p1", "models": ["m1", "m2"], "macro": "risk-on",
    "signals": [{"sector": WATER, "confidence": 0.8, "convergence_type": "Convergent", "rationale": "why",
                 "conviction_drivers": ["Headline text from a newspaper"], "retail_thesis": "a Reddit paraphrase"}],
    "trace": {"feeds": [{"name": "Feed", "fetched": 10, "kept": 8, "ok": True}]},
}


def test_publish_site_writes_portfolio_ledger_and_trace_without_headline_text():
    s3 = FakeS3()
    store = ps.Store(s3, "b")
    run_week(store, "2026-W44", W44)
    run_week(store, "2026-W45", snap(row("AAA", 220.0, candidate=True)))
    trace = ps.build_trace("2026-W45", SCOUT, snap(row("AAA", 220.0, candidate=True)), email_sent=True, now=lambda: NOW)
    ps.publish_site(store, "2026-W45", trace, now=lambda: NOW)

    site = s3.json("site/portfolio.json")
    assert site["week"] == "2026-W45" and [h["week"] for h in site["history"]] == ["2026-W44", "2026-W45"]
    [pos] = site["positions"]
    assert pos["ticker"] == "AAA" and pos["weeks_held"] == 1 and pos["weeks_to_max_hold"] == 5
    assert pos["pnl_gbp"] == pytest.approx(88.0 - 80.4)            # £80 bought at 200p (+0.5% duty), now 220p
    assert site["rules"]["max_positions"] == 8 and site["starting_cash"] == 1000.0
    assert [t["ticker"] for t in s3.json("site/ledger.json")["trades"]] == ["AAA"]
    assert s3.json("site/weeks/index.json")["weeks"] == ["2026-W45"]
    published = s3.objects["site/weeks/2026-W45.json"].decode()
    assert "Headline text" not in published and "Reddit paraphrase" not in published
    week = json.loads(published)
    assert week["scout"]["trace"]["feeds"][0]["fetched"] == 10
    assert week["selection"][0]["sector"] == WATER and week["selection"][0]["candidates"] == 1
    assert week["email"] == {"sent": True}


def test_trace_without_the_scouts_trace_block():
    trace = ps.build_trace("2026-W44", {**SCOUT, "trace": None}, W44, now=lambda: NOW)
    assert trace["scout"]["trace"] is None
    assert trace["prices"]["requested"] == 3 and trace["analysis"]["analysed"] == 3


def test_quarter_summary():
    s3 = FakeS3()
    store = ps.Store(s3, "b")
    run_week(store, "2026-W44", W44)
    run_week(store, "2026-W45", snap(row("AAA", 150.0)))
    summary = ps.quarter_summary(ps.load_records(s3, "b"))
    since = summary["since_start"]
    assert since["weeks"] == 2 and since["buys"] == 1 and since["sells"] == 1
    assert since["exits_by_reason"] == {"stop": 1}
    assert since["value_end"] < 1000
    assert ps.quarter_summary([]) is None


# ---------------------------------------------------------------------------
# Through the Lambda handler
# ---------------------------------------------------------------------------

SIGNAL_TS = datetime(2026, 10, 31, 6, 0, 3, tzinfo=timezone.utc)
KEY = "successful/2026-W44/response_20261031T060000Z.json"


def synthetic_raw(tickers):
    index = pd.bdate_range(end="2026-10-30", periods=130)
    raw = {}
    for t in tickers:
        rng = np.random.default_rng(zlib.crc32(t.encode()))
        close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.015, len(index))))
        raw[t] = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close,
                               "Volume": rng.integers(100_000, 2_000_000, len(index))}, index=index)
    return raw


RAW = None


def fetch_prices(ts):
    global RAW
    tickers = universe_tickers()
    RAW = RAW or synthetic_raw(tickers)
    return clean_prices(RAW, cutoff_date(ts), tickers)


def good_analysis(data):
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
    monkeypatch.setattr(handler, "analyse_universe", good_analysis)


def lambda_run(s3, key=KEY, ts=SIGNAL_TS, fetch_fx=fx(1.3), ses=None):
    ses = ses or FakeSES()
    s3.objects[key] = json.dumps({**SCOUT, "signals": [
        {**SCOUT["signals"][0], "sector": "Housebuilders", "timestamp": ts.isoformat()}]}).encode()
    out = handler.run({"Records": [{"s3": {"bucket": {"name": "bkt"}, "object": {"key": key}}}]}, None, s3, ses,
                      fetch=fetch_prices, sleep=lambda s: None, now=lambda: ts, fetch_fx=fetch_fx)
    return out["processed"][0], ses


def test_lambda_applies_the_week_emails_the_portfolio_and_publishes_the_site():
    s3 = FakeS3()
    result, ses = lambda_run(s3)
    assert result["status"] == "ok" and result["portfolio"] == "ok"
    record = json.loads(s3.objects["portfolio/weeks/2026-W44.json"])
    assert record["gbpusd"] == 1.3 and record["source_key"] == KEY
    assert len(record["result"]["trades"]) == result["picks"] == 2   # Housebuilders at 0.8 confidence -> 2 Picks
    [mail] = ses.sent
    html_body, text_body = mail["Message"]["Body"]["Html"]["Data"], mail["Message"]["Body"]["Text"]["Data"]
    assert "PAPER PORTFOLIO" in html_body and "PAPER PORTFOLIO" in text_body
    assert ps.SITE_URL_DEFAULT in html_body
    for key in ("site/portfolio.json", "site/ledger.json", "site/weeks/index.json", "site/weeks/2026-W44.json"):
        assert key in s3.objects
    assert json.loads(s3.objects["site/weeks/2026-W44.json"])["selection"][0]["picks_allowed"] == 2


def test_lambda_portfolio_failure_still_sends_the_email():
    s3 = FakeS3()
    ts = datetime(2026, 11, 7, 6, 0, tzinfo=timezone.utc)            # W45 with no W44 record
    result, ses = lambda_run(s3, key="successful/2026-W45/response_20261107T060000Z.json", ts=ts)
    assert result["status"] == "ok" and result["portfolio"] == "failed"
    [mail] = ses.sent
    assert "Not updated this week" in mail["Message"]["Body"]["Text"]["Data"]
    assert "site/portfolio.json" not in s3.objects                   # nothing to publish yet


def test_lambda_without_an_exchange_rate_still_trades_lse():
    def no_fx(on):
        raise TimeoutError("hung")
    s3 = FakeS3()
    result, _ = lambda_run(s3, fetch_fx=no_fx)
    record = json.loads(s3.objects["portfolio/weeks/2026-W44.json"])
    assert record["gbpusd"] is None
    assert any(n["kind"] == "missing_fx" for n in record["result"]["notes"])


def test_lambda_site_publish_failure_does_not_fail_the_week():
    s3 = FakeS3()
    s3.fail_put_for = ("site/",)
    result, ses = lambda_run(s3)
    assert result["status"] == "ok" and len(ses.sent) == 1


def test_stored_week_invocation_rebuilds_without_prices_or_email():
    s3 = FakeS3()
    lambda_run(s3)
    del s3.objects["portfolio/weeks/2026-W44.json"]
    ses = FakeSES()
    out = handler.run({"portfolio_week": "2026-W44", "bucket": "bkt"}, None, s3, ses,
                      fetch=lambda ts: pytest.fail("no price download"), now=lambda: NOW, fetch_fx=fx(1.3))
    assert out["status"] == "ok" and out["trades"] == 2 and out["gbpusd"] == 1.3
    assert ses.sent == []
    assert json.loads(s3.objects["site/weeks/2026-W44.json"])["scout"]["macro"] == "risk-on"


def test_stored_week_invocation_reports_a_missing_snapshot():
    out = handler.run({"portfolio_week": "2026-W44", "bucket": "bkt"}, None, FakeS3(), FakeSES(), now=lambda: NOW,
                      fetch_fx=fx())
    assert out["status"] == "failed" and "no Universe Snapshot" in out["error"]
