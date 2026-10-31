import importlib
import sys
import time
from pathlib import Path

import pytest

SCOUT_DIR = Path(__file__).resolve().parents[1] / "financial_market_news_analyzer"


@pytest.fixture
def scout(monkeypatch):
    monkeypatch.syspath_prepend(str(SCOUT_DIR))
    for name in ("sector_scout", "reddit_fetcher", "reddit_analyzer", "news_fetcher", "llm_analyzer",
                 "consolidator", "config"):
        sys.modules.pop(name, None)
    return importlib.import_module("sector_scout")


def test_reddit_is_collected_while_the_news_pass_runs_and_reaches_the_reddit_llm_pass(scout, monkeypatch):
    events = []

    def slow_reddit(max_total, stop):
        events.append("reddit start")
        time.sleep(0.2)                                  # Reddit's rate limit: the news work finishes first
        events.append("reddit done")
        return ["post"]

    def news_headlines(max_total):
        events.append("headlines")
        return ["headline"]

    def news_llm(headlines):
        events.append("news llm")
        return [], True

    def reddit_llm(posts):
        events.append(f"reddit llm {posts}")
        return [], True

    monkeypatch.setattr(scout, "collect_reddit_posts", slow_reddit)
    monkeypatch.setattr(scout, "collect_headlines", news_headlines)
    monkeypatch.setattr(scout, "analyse_headlines", news_llm)
    monkeypatch.setattr(scout, "analyse_reddit_posts", reddit_llm)
    monkeypatch.setattr(scout, "consolidate_signals", lambda **kw: ([], True))

    scout.SectorScout(on_signal=lambda signals: None).run_cycle()

    assert events == ["reddit start", "headlines", "news llm", "reddit done", "reddit llm ['post']"]


def test_reddit_collection_is_told_to_stop_when_there_are_no_headlines(scout, monkeypatch):
    stops = []
    monkeypatch.setattr(scout, "collect_reddit_posts", lambda max_total, stop: stops.append(stop) or [])
    monkeypatch.setattr(scout, "collect_headlines", lambda max_total: [])

    assert scout.SectorScout().run_cycle() == ([], False)
    assert stops[0].is_set()


def test_a_reddit_collection_that_raises_does_not_stop_the_cycle(scout, monkeypatch):
    def boom(max_total, stop):
        raise RuntimeError("reddit down")

    monkeypatch.setattr(scout, "collect_reddit_posts", boom)
    monkeypatch.setattr(scout, "collect_headlines", lambda max_total: ["headline"])
    monkeypatch.setattr(scout, "analyse_headlines", lambda headlines: ([], True))
    monkeypatch.setattr(scout, "consolidate_signals", lambda **kw: ([], True))

    assert scout.SectorScout(on_signal=lambda signals: None).run_cycle() == ([], True)
