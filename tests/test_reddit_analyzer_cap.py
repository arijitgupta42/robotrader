import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCOUT_DIR = Path(__file__).resolve().parents[1] / "financial_market_news_analyzer"
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def analyzer(monkeypatch):
    monkeypatch.syspath_prepend(str(SCOUT_DIR))
    for name in ("reddit_analyzer", "reddit_fetcher", "llm_analyzer", "config"):
        sys.modules.pop(name, None)
    return importlib.import_module("reddit_analyzer")


def posts(analyzer, subreddit, count):
    return [analyzer.RedditPost(subreddit=subreddit, title="t", selftext="", score=0, upvote_ratio=0.0,
                                num_comments=0, url="", permalink="", post_id=f"{subreddit}{i}", flair="",
                                created_utc=NOW - timedelta(minutes=i)) for i in range(count)]


def test_the_llm_pass_gets_the_configured_number_of_posts_with_a_fair_share_per_subreddit(analyzer, monkeypatch):
    seen = []
    monkeypatch.setattr(analyzer, "_build_reddit_messages", lambda p: seen.extend(p) or [])
    monkeypatch.setattr(analyzer, "_invoke_with_fallback", lambda messages: (None, None))
    cfg = analyzer.REDDIT_CFG

    assert cfg.max_posts_for_llm == 120
    analyzer.analyse_reddit_posts(posts(analyzer, "wallstreetbets", 150) + posts(analyzer, "geopolitics", 50))

    assert len(seen) == cfg.max_posts_for_llm
    assert {p.subreddit for p in seen} == {"wallstreetbets", "geopolitics"}
    assert sum(1 for p in seen if p.subreddit == "geopolitics") == 50     # the quiet one is not crowded out
