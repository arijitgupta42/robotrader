import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stock_selection_lambda"))

import backtest_handler  # noqa: E402
from lse_stock_analysis import backtest  # noqa: E402

NOW = datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc)


def snapshot_csv(closes, cands=()):
    rows = [{"ticker": t, "close": c, "is_candidate": t in cands, "is_pick": t == "A", "signal_confidence": 0.7 if t in cands else None,
             "signal_convergence_type": "News-Led" if t in cands else None, "setup_grade": "B", "swing_setup": "momentum",
             "primary_sector": "Housebuilders", "methodology_version": "m1", "prompt_version": "p1"} for t, c in closes.items()]
    return pd.DataFrame(rows).to_csv(index=False).encode()


class FakeS3:
    def __init__(self, objects=None, fail_put_for=()):
        self.objects, self.fail_put_for = dict(objects or {}), fail_put_for

    def get_paginator(self, name):
        objs = self.objects

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in sorted(objs) if k.startswith(Prefix)]}
        return P()

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        if any(Key.startswith(p) for p in self.fail_put_for):
            raise OSError("S3 down")
        self.objects[Key] = Body


class FakeSES:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_email(self, **kw):
        if self.fail:
            raise RuntimeError("SES down")
        self.sent.append(kw)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("BUCKET_NAME", "bkt")
    monkeypatch.setenv("SES_SENDER", "from@example.com")
    monkeypatch.setenv("SES_RECIPIENT", "to@example.com")


def history(weeks=4):
    objs, label = {}, "2026-W36"
    for i in range(weeks):
        objs[f"snapshots/{label}/universe.csv"] = snapshot_csv({"A": 100 * 1.01 ** i, "B": 100 * 0.99 ** i, "C": 100}, cands=("A", "B"))
        label = backtest.week_offset(label, 1)
    return objs


def test_period_label():
    assert backtest_handler.period_label(datetime(2026, 1, 1, tzinfo=timezone.utc)) == "2026-Q1"
    assert backtest_handler.period_label(datetime(2026, 10, 1, tzinfo=timezone.utc)) == "2026-Q4"
    assert backtest_handler.period_label(datetime(2026, 6, 30, tzinfo=timezone.utc)) == "2026-Q2"


def test_report_is_saved_and_emailed_once():
    s3, ses = FakeS3(history()), FakeSES()
    out = backtest_handler.run({}, None, s3, ses, now=lambda: NOW)
    assert out["status"] == "ok" and out["period"] == "2026-Q4" and out["weeks"] == 4
    assert "reports/2026-Q4/backtest.html" in s3.objects
    saved = json.loads(s3.objects["reports/2026-Q4/backtest.json"])
    assert saved["horizons"] == [2, 4, 6] and "headline" in saved and "2" in saved["headline"]["Picks"]      # int keys became strings
    assert len(ses.sent) == 1
    mail = ses.sent[0]
    assert mail["Source"] == "from@example.com" and mail["Destination"] == {"ToAddresses": ["to@example.com"]}
    assert mail["Message"]["Subject"]["Data"] == "Methodology review: backtest report 2026-Q4"
    assert "methodology-review.md" in mail["Message"]["Body"]["Html"]["Data"]
    assert "Picks vs Candidates vs Universe" in mail["Message"]["Body"]["Text"]["Data"]


def test_a_period_can_be_given_in_the_event():
    out = backtest_handler.run({"period": "2027-Q1"}, None, FakeS3(history()), FakeSES(), now=lambda: NOW)
    assert out["period"] == "2027-Q1"


def test_too_few_snapshots_is_a_recorded_and_emailed_failure_not_an_exception():
    s3, ses = FakeS3(history(weeks=1)), FakeSES()
    out = backtest_handler.run({}, None, s3, ses, now=lambda: NOW)
    assert out["status"] == "failed" and "nothing to measure" in out["error"]
    assert out["failed_key"].startswith("reports/2026-Q4/failed_") and out["failed_key"] in s3.objects
    assert len(ses.sent) == 1 and ses.sent[0]["Message"]["Subject"]["Data"] == "Methodology review FAILED: 2026-Q4"


def test_failures_in_saving_or_emailing_never_raise():
    out = backtest_handler.run({}, None, FakeS3(history(), fail_put_for=("reports/",)), FakeSES(), now=lambda: NOW)
    assert out["status"] == "failed" and out["failed_key"] is None
    out = backtest_handler.run({}, None, FakeS3(history()), FakeSES(fail=True), now=lambda: NOW)
    assert out["status"] == "failed"


def test_report_includes_the_paper_portfolio():
    import portfolio_step
    s3 = FakeS3(history())
    store = portfolio_step.Store(s3, "bkt")
    w44 = pd.DataFrame([{"ticker": "A", "market": "LSE", "close": 200.0, "primary_sector": "Housebuilders", "status": "ok",
                         "is_candidate": True, "is_pick": True, "signal_confidence": 0.8, "signal_convergence_type": "News-Led",
                         "setup_grade": "A", "risk_score": 3, "max_position_size_pct": 10.0, "atr_stop_pct": 6.0,
                         "price_cutoff": "2026-10-30"}])
    portfolio_step.run_week(store, "2026-W44", w44, 1.3, now=lambda: NOW)
    ses = FakeSES()
    out = backtest_handler.run({}, None, s3, ses, now=lambda: NOW)
    assert out["status"] == "ok"
    saved = json.loads(s3.objects["reports/2026-Q4/backtest.json"])
    assert saved["paper_portfolio"]["since_start"]["buys"] == 1
    assert "PAPER PORTFOLIO" in ses.sent[0]["Message"]["Body"]["Text"]["Data"]
    assert "PAPER PORTFOLIO" in ses.sent[0]["Message"]["Body"]["Html"]["Data"]


def test_report_without_portfolio_records_still_works():
    ses = FakeSES()
    out = backtest_handler.run({}, None, FakeS3(history()), ses, now=lambda: NOW)
    assert out["status"] == "ok"
    assert "No weeks recorded yet" in ses.sent[0]["Message"]["Body"]["Text"]["Data"]
