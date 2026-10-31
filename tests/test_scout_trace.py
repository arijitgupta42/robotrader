import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

SCOUT_DIR = Path(__file__).resolve().parents[1] / "financial_market_news_analyzer"
NOW = datetime.now(timezone.utc)


def fresh_import(monkeypatch, name):
    monkeypatch.syspath_prepend(str(SCOUT_DIR))
    for mod in (name, "run_trace", "config"):
        sys.modules.pop(mod, None)
    return importlib.import_module(name)


# ---------------------------------------------------------------------------
# The recorder
# ---------------------------------------------------------------------------

def test_reset_records_and_copies(monkeypatch):
    rt = fresh_import(monkeypatch, "run_trace")
    rt.reset("WeeklyTrigger")
    rt.feed("A", 3, ok=True)
    rt.feed("B", 0, ok=False, error="DNS lookup failed")
    rt.feed_counts(fresh={"A": 2}, kept={"A": 1})
    rt.llm_pass("news", ["primary/model", "fallback/model"], "primary/model-2026-10", True, 12.34)
    rt.llm_pass("consolidation", ["primary/model", "fallback/model"], "fallback/model", True, 3.0)
    rt.llm_pass("reddit", ["primary/model"], None, False, 1.0)
    rt.timing("news_fetch", 20.06)
    out = rt.to_dict()
    assert out["trigger"] == "WeeklyTrigger"
    assert out["feeds"] == [{"name": "A", "fetched": 3, "ok": True, "error": None, "fresh": 2, "kept": 1},
                            {"name": "B", "fetched": 0, "ok": False, "error": "DNS lookup failed", "fresh": 0, "kept": 0}]
    assert out["llm"]["news"]["fallback"] is False and out["llm"]["news"]["seconds"] == 12.3
    assert out["llm"]["consolidation"]["fallback"] is True
    assert out["llm"]["reddit"] == {"models": ["primary/model"], "model_used": None, "ok": False, "seconds": 1.0,
                                    "fallback": False}
    assert out["timings"]["news_fetch"] == 20.1 and "total" in out["timings"]
    out["feeds"].clear()
    assert len(rt.to_dict()["feeds"]) == 2                             # a copy, not the live trace


def test_recording_never_raises(monkeypatch):
    rt = fresh_import(monkeypatch, "run_trace")
    rt._trace = None                                                    # broken state
    rt.feed("A", 1, ok=True)
    rt.llm_pass("news", ["m"], "m", True, 1.0)


# ---------------------------------------------------------------------------
# News feeds and the sieve
# ---------------------------------------------------------------------------

def entry(title, age_days=0):
    return SimpleNamespace(title=title, summary="", link="https://x/" + title,
                           published_parsed=(NOW - timedelta(days=age_days)).timetuple())


def test_feeds_and_the_sieve_are_traced_without_headline_text(monkeypatch):
    nf = fresh_import(monkeypatch, "news_fetcher")
    rt = nf.run_trace
    rt.reset("manual")
    monkeypatch.setattr(nf, "RSS_FEEDS", [{"name": "Good", "url": "https://good/rss"},
                                          {"name": "Other", "url": "https://other/rss"},
                                          {"name": "Down", "url": "https://bad/rss"}])
    monkeypatch.setattr(nf, "_host_reachable", lambda url, timeout=3.0: "bad" not in url)
    feeds = {
        "https://good/rss": [entry("Secret alpha headline"), entry("Secret beta headline"), entry("Secret old headline", 30)],
        "https://other/rss": [entry("Secret alpha headline"), entry("Secret gamma headline")],
    }
    monkeypatch.setattr(nf.feedparser, "parse", lambda url, request_headers=None:
                        SimpleNamespace(bozo=False, bozo_exception=None, entries=feeds[url]))
    monkeypatch.setattr(nf, "fetch_hl_weekly_outlook", lambda: [])
    monkeypatch.setattr(nf.time, "sleep", lambda s: None)

    kept = nf.collect_headlines(max_total=2)
    assert len(kept) == 2
    trace = rt.to_dict()
    by_name = {f["name"]: f for f in trace["feeds"]}
    assert by_name["Good"] == {"name": "Good", "fetched": 3, "ok": True, "error": None, "fresh": 2,
                               "kept": by_name["Good"]["kept"]}
    assert by_name["Down"]["ok"] is False and by_name["Down"]["error"] == "DNS lookup failed"
    assert by_name["HL Weekly Outlook"]["ok"] is False
    assert by_name["Good"]["kept"] + by_name["Other"]["kept"] == 2
    assert trace["headlines"] == {"fetched": 5, "duplicates": 1, "stale": 1, "fresh": 3, "cap": 2, "kept": 2,
                                  "max_age_days": 7}
    assert "Secret" not in json.dumps(trace)


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------

def test_reddit_requests_are_traced_even_when_nothing_comes_back(monkeypatch):
    rf = fresh_import(monkeypatch, "reddit_fetcher")
    rt = rf.run_trace
    rt.reset("manual")
    responses = [rf._Response(status=429, remaining=0.0, reset_s=1.0), rf._Response(status=429, remaining=0.0, reset_s=1.0),
                 rf._Response(status=0, error="connection refused")]
    monkeypatch.setattr(rf, "REDDIT_REQUESTS", [{"subreddits": ["stocks"], "sort": "hot"},
                                                {"subreddits": ["investing"], "sort": "new"}])
    monkeypatch.setattr(rf, "_http_get", lambda url: responses.pop(0))
    monkeypatch.setattr(rf, "_pause", lambda seconds, stop: False)
    clock = iter(range(1000))
    monkeypatch.setattr(rf, "_clock", lambda: float(next(clock)))
    assert rf.collect_reddit_posts() == []
    reddit = rt.to_dict()["reddit"]
    assert reddit["planned"] == 2 and reddit["succeeded"] == 0 and reddit["kept"] == 0
    assert [(r["label"], r["status"], r["retried"]) for r in reddit["requests"]] == [
        ("stocks/hot", "throttled", True), ("investing/new", "failed", False)]


# ---------------------------------------------------------------------------
# LLM passes
# ---------------------------------------------------------------------------

def test_a_fallback_model_answering_the_news_pass_is_traced(monkeypatch):
    la = fresh_import(monkeypatch, "llm_analyzer")
    rt = la.run_trace
    rt.reset("manual")
    fallback = la.OPENROUTER_MODELS[-1]
    monkeypatch.setattr(la, "_invoke_with_fallback", lambda messages: ({"macro": {"regime": "m"}, "signals": []}, fallback))
    headline = la.Headline(source="S", title="t", summary="", url="", published=NOW)
    la.analyse_headlines([headline])
    news = rt.to_dict()["llm"]["news"]
    assert news["model_used"] == fallback and news["ok"] is True
    assert news["fallback"] is (len(la.OPENROUTER_MODELS) > 1)
    assert news["models"] == list(la.OPENROUTER_MODELS)


def test_all_models_failing_is_traced(monkeypatch):
    la = fresh_import(monkeypatch, "llm_analyzer")
    rt = la.run_trace
    rt.reset("manual")
    monkeypatch.setattr(la, "_invoke_with_fallback", lambda messages: (None, None))
    la.analyse_headlines([la.Headline(source="S", title="t", summary="", url="", published=NOW)])
    assert rt.to_dict()["llm"]["news"]["ok"] is False


# ---------------------------------------------------------------------------
# The Lambda handler saves the trace with the result
# ---------------------------------------------------------------------------

@pytest.fixture
def scout(monkeypatch):
    for name, value in dict(BUCKET_NAME="bkt", AWS_DEFAULT_REGION="eu-west-1", AWS_ACCESS_KEY_ID="x",
                            AWS_SECRET_ACCESS_KEY="x").items():
        monkeypatch.setenv(name, value)
    module = fresh_import(monkeypatch, "lambda_handler")

    class Fake:
        def __getattr__(self, name):
            return lambda **kw: None

    monkeypatch.setattr(module, "ssm", Fake())
    yield module
    sys.modules.pop("lambda_handler", None)


@pytest.mark.parametrize("success", [True, False])
def test_the_result_carries_the_trace(scout, monkeypatch, success):
    saved = []
    monkeypatch.setattr(scout, "_save_success", lambda payload: saved.append(payload) or "k")
    monkeypatch.setattr(scout, "_save_failure", lambda payload, reason: saved.append(payload) or "k")

    def pipeline():
        scout.run_trace.feed("A", 4, ok=True)
        return {"success": success, "signals": [{"sector": "Water"}], "macro": "m", "error": None if success else "x"}

    monkeypatch.setattr(scout, "_run_pipeline", pipeline)
    scout.handler({"detail-type": "WeeklyTrigger"}, None)
    [payload] = saved
    assert payload["trace"]["trigger"] == "WeeklyTrigger"
    assert payload["trace"]["feeds"][0]["fetched"] == 4
