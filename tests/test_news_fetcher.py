import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCOUT_DIR = Path(__file__).resolve().parents[1] / "financial_market_news_analyzer"
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def news_fetcher(monkeypatch):
    monkeypatch.syspath_prepend(str(SCOUT_DIR))
    for name in ("news_fetcher", "config"):
        sys.modules.pop(name, None)
    return importlib.import_module("news_fetcher")


def headlines(news_fetcher, source, count, minutes_apart=1):
    return [news_fetcher.Headline(source=source, title=f"{source} {i}", summary="", url="",
                                  published=NOW - timedelta(minutes=i * minutes_apart))
            for i in range(count)]


def test_under_the_cap_nothing_is_dropped(news_fetcher):
    items = headlines(news_fetcher, "A", 3) + headlines(news_fetcher, "B", 2)
    assert news_fetcher._cap_balanced(items, 10) == items


def test_a_busy_source_does_not_crowd_out_quiet_ones(news_fetcher):
    # The busy source's headlines are all newer than the quiet sources', so a plain newest-first cut would keep only it.
    busy = headlines(news_fetcher, "Busy", 100)
    quiet = headlines(news_fetcher, "QuietA", 5, minutes_apart=60) + headlines(news_fetcher, "QuietB", 5, minutes_apart=60)
    kept = news_fetcher._cap_balanced(busy + quiet, 30)
    by_source = {s: sum(1 for h in kept if h.source == s) for s in ("Busy", "QuietA", "QuietB")}
    assert len(kept) == 30
    assert by_source == {"Busy": 20, "QuietA": 5, "QuietB": 5}


def test_each_source_keeps_its_newest_headlines_and_the_result_is_newest_first(news_fetcher):
    kept = news_fetcher._cap_balanced(headlines(news_fetcher, "A", 10) + headlines(news_fetcher, "B", 10), 6)
    assert [h.title for h in kept if h.source == "A"] == ["A 0", "A 1", "A 2"]
    assert [h.published for h in kept] == sorted((h.published for h in kept), reverse=True)


def test_every_feed_in_the_config_is_unique_and_well_formed(news_fetcher):
    config = sys.modules["config"]
    urls = [f["url"] for f in config.RSS_FEEDS]
    names = [f["name"] for f in config.RSS_FEEDS]
    assert len(set(urls)) == len(urls) and len(set(names)) == len(names)
    assert all(url.startswith("https://") for url in urls)
    subs = [s["subreddit"] for s in config.REDDIT_SUBREDDITS]
    assert len(set(subs)) == len(subs)
