import importlib
import sys
from pathlib import Path

import pytest

SCOUT_DIR = Path(__file__).resolve().parents[1] / "financial_market_news_analyzer"


@pytest.fixture
def scout(monkeypatch):
    """The scout's lambda_handler imported with fake env vars; its S3 and SSM clients replaced by fakes."""
    for name, value in dict(BUCKET_NAME="bkt",
                            AWS_DEFAULT_REGION="eu-west-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x").items():
        monkeypatch.setenv(name, value)
    monkeypatch.syspath_prepend(str(SCOUT_DIR))
    sys.modules.pop("lambda_handler", None)
    module = importlib.import_module("lambda_handler")

    class FakeS3:
        def __init__(self):
            self.puts = []

        def put_object(self, **kw):
            self.puts.append(kw["Key"])

    class Fake:
        def __init__(self):
            self.calls = []

        def __getattr__(self, name):
            return lambda **kw: self.calls.append((name, kw))

    monkeypatch.setattr(module, "s3", FakeS3())
    monkeypatch.setattr(module, "ssm", Fake())
    yield module
    sys.modules.pop("lambda_handler", None)


def test_a_successful_run_writes_only_the_success_record_and_sends_no_email(scout, monkeypatch):
    signals = [{"sector": "Housebuilders", "confidence": 0.7, "convergence_type": "News-Led"}]
    monkeypatch.setattr(scout, "_run_pipeline", lambda: {"success": True, "signals": signals, "macro": "m"})
    out = scout.handler({}, None)
    assert out["statusCode"] == 200
    assert len(scout.s3.puts) == 1 and scout.s3.puts[0].startswith("successful/")
    assert not any(k.startswith("outcomes/") for k in scout.s3.puts)
    assert not hasattr(scout, "ses") and not hasattr(scout, "_build_email")   # the stock-selection Lambda sends the one email


def test_the_outcome_stub_writer_is_gone(scout):
    assert not hasattr(scout, "_save_outcome_stubs")
