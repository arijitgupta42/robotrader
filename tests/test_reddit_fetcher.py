import importlib
import logging
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCOUT_DIR = Path(__file__).resolve().parents[1] / "financial_market_news_analyzer"
NOW = datetime.now(timezone.utc)


def feed_xml(posts):
    """posts: [(subreddit, post_id, minutes_old)] as a Reddit-style Atom feed."""
    entries = "".join(
        f"<entry><id>t3_{pid}</id><title>post {pid}</title>"
        f"<link href=\"https://www.reddit.com/r/{sub}/comments/{pid}/post/\"/>"
        f"<updated>{(NOW - timedelta(minutes=age)).strftime('%Y-%m-%dT%H:%M:%S+00:00')}</updated>"
        f"<content type=\"html\">body {pid}</content></entry>"
        for sub, pid, age in posts
    )
    return f"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>{entries}</feed>".encode()


class FakeReddit:
    """Stands in for the HTTP layer, the sleep and the clock: nothing touches the network or really waits."""

    def __init__(self, rf, responses):
        self.rf, self.responses, self.urls, self.pauses, self.now = rf, list(responses), [], [], 0.0

    def get(self, url):
        self.urls.append(url)
        self.now += 0.5
        return self.responses.pop(0)

    def pause(self, seconds, stop):
        self.pauses.append(seconds)
        self.now += seconds
        return bool(stop and stop.is_set())

    def ok(self, posts, remaining=0.0, reset_s=58.0):
        return self.rf._Response(status=200, body=feed_xml(posts), remaining=remaining, reset_s=reset_s)

    def throttled(self, reset_s=20.0):
        return self.rf._Response(status=429, remaining=0.0, reset_s=reset_s)


@pytest.fixture
def fetcher(monkeypatch):
    monkeypatch.syspath_prepend(str(SCOUT_DIR))
    for name in ("reddit_fetcher", "config"):
        sys.modules.pop(name, None)
    rf = importlib.import_module("reddit_fetcher")

    def build(requests_, responses):
        fake = FakeReddit(rf, responses)
        monkeypatch.setattr(rf, "REDDIT_REQUESTS", requests_)
        monkeypatch.setattr(rf, "_http_get", fake.get)
        monkeypatch.setattr(rf, "_pause", fake.pause)
        monkeypatch.setattr(rf, "_clock", lambda: fake.now)
        return rf, fake

    return build


ONE = [{"subreddits": ["stocks"], "sort": "hot"}]
TWO = [{"subreddits": ["stocks"], "sort": "hot"}, {"subreddits": ["UKInvesting"], "sort": "hot"}]


def test_a_429_is_logged_as_a_warning_and_retried_once_after_the_reset(fetcher, caplog):
    rf, fake = fetcher(ONE, [None, None])
    fake.responses = [fake.throttled(reset_s=20), fake.ok([("stocks", "a", 1)])]
    with caplog.at_level(logging.INFO):
        posts = rf.collect_reddit_posts()
    assert [p.post_id for p in posts] == ["a"]
    assert len(fake.urls) == 2
    assert fake.pauses == [pytest.approx(20 + rf.REDDIT_CFG.reset_margin_s)]
    assert any("HTTP 429" in r.message and r.levelno == logging.WARNING for r in caplog.records)


def test_a_request_still_throttled_after_the_retry_is_skipped_loudly_not_silently(fetcher, caplog):
    rf, fake = fetcher(ONE, [None, None])
    fake.responses = [fake.throttled(), fake.throttled()]
    with caplog.at_level(logging.INFO):
        posts = rf.collect_reddit_posts()
    assert posts == [] and len(fake.urls) == 2
    assert any("failed (HTTP 429)" in r.message for r in caplog.records)
    assert any("0 of 1 requests returned posts" in r.message for r in caplog.records)


def test_the_next_request_waits_for_the_reset_the_response_asked_for(fetcher):
    rf, fake = fetcher(TWO, [None, None])
    fake.responses = [fake.ok([("stocks", "a", 1)], reset_s=58), fake.ok([("UKInvesting", "b", 2)])]
    posts = rf.collect_reddit_posts()
    assert {p.post_id for p in posts} == {"a", "b"}
    assert fake.pauses == [pytest.approx(58 + rf.REDDIT_CFG.reset_margin_s)]


def test_without_rate_limit_headers_the_fallback_gap_is_used(fetcher):
    rf, fake = fetcher(TWO, [None, None])
    bare = rf._Response(status=200, body=feed_xml([("stocks", "a", 1)]))
    fake.responses = [bare, fake.ok([("UKInvesting", "b", 2)])]
    rf.collect_reddit_posts()
    assert fake.pauses == [rf.REDDIT_CFG.fallback_gap_s]


def test_a_bad_reset_header_cannot_stall_the_pass(fetcher):
    rf, fake = fetcher(TWO, [None, None])
    fake.responses = [fake.ok([("stocks", "a", 1)], reset_s=100000), fake.ok([("UKInvesting", "b", 2)])]
    rf.collect_reddit_posts()
    assert fake.pauses == [rf.REDDIT_CFG.max_wait_s]


def test_the_time_budget_drops_the_last_requests_and_says_so(fetcher, monkeypatch, caplog):
    rf, fake = fetcher(TWO * 2, [None] * 4)
    fake.responses = [fake.ok([("stocks", f"p{i}", 1)], reset_s=58) for i in range(4)]
    monkeypatch.setattr(rf.REDDIT_CFG, "time_budget_s", 100.0)
    with caplog.at_level(logging.INFO):
        posts = rf.collect_reddit_posts()
    assert len(fake.urls) == 2 and len(posts) == 2          # the 3rd request would start at ~119 s
    assert any("time budget" in r.message and "skipping the last 2 of 4" in r.message for r in caplog.records)


def test_a_set_stop_event_ends_the_pass_without_more_requests(fetcher):
    rf, fake = fetcher(TWO, [None])
    fake.responses = [fake.ok([("stocks", "a", 1)])]
    stop = threading.Event()
    stop.set()
    assert rf.collect_reddit_posts(stop=stop) == []
    assert fake.urls == []


def test_a_network_error_is_a_warning_and_the_pass_carries_on(fetcher, caplog):
    rf, fake = fetcher(TWO, [None, None])
    fake.responses = [rf._Response(status=0, error="timed out"), fake.ok([("UKInvesting", "b", 2)])]
    with caplog.at_level(logging.INFO):
        posts = rf.collect_reddit_posts()
    assert [p.post_id for p in posts] == ["b"]
    assert any("timed out" in r.message for r in caplog.records)
    assert any("1 of 2 requests returned posts" in r.message for r in caplog.records)


def test_urls_ask_for_100_posts_combine_subreddits_and_name_the_top_window(fetcher):
    reqs = [{"subreddits": ["stocks", "investing"], "sort": "hot"},
            {"subreddits": ["stocks", "investing"], "sort": "top", "t": "week"}]
    rf, fake = fetcher(reqs, [None, None])
    fake.responses = [fake.ok([]), fake.ok([])]
    rf.collect_reddit_posts()
    assert fake.urls == ["https://www.reddit.com/r/stocks+investing/hot.rss?limit=100",
                         "https://www.reddit.com/r/stocks+investing/top.rss?limit=100&t=week"]


def test_a_combined_feed_reads_each_posts_subreddit_from_its_link_and_dedupes_across_requests(fetcher):
    reqs = [{"subreddits": ["stocks", "investing"], "sort": "hot"},
            {"subreddits": ["stocks", "investing"], "sort": "top", "t": "week"}]
    rf, fake = fetcher(reqs, [None, None])
    fake.responses = [fake.ok([("stocks", "a", 1), ("investing", "b", 2)]),
                      fake.ok([("investing", "b", 2), ("stocks", "c", 3)])]
    posts = rf.collect_reddit_posts()
    assert {(p.post_id, p.subreddit) for p in posts} == {("a", "stocks"), ("b", "investing"), ("c", "stocks")}


def test_the_cap_gives_each_subreddit_a_fair_share_newest_first(fetcher):
    rf, _ = fetcher(ONE, [])
    mk = lambda sub, i, age: rf.RedditPost(subreddit=sub, title="t", selftext="", score=0, upvote_ratio=0.0,
                                           num_comments=0, url="", permalink="", post_id=f"{sub}{i}", flair="",
                                           created_utc=NOW - timedelta(minutes=age))
    busy = [mk("wsb", i, i) for i in range(50)]                   # all newer than the quiet sub's posts
    quiet = [mk("UKInvesting", i, 1000 + i) for i in range(5)]
    kept = rf.cap_balanced(busy + quiet, 12)
    by_sub = {s: sum(1 for p in kept if p.subreddit == s) for s in ("wsb", "UKInvesting")}
    assert len(kept) == 12 and by_sub == {"wsb": 7, "UKInvesting": 5}
    assert [p.created_utc for p in kept] == sorted((p.created_utc for p in kept), reverse=True)


def test_posts_older_than_the_age_limit_are_dropped(fetcher, caplog):
    rf, fake = fetcher(ONE, [None])
    day = 24 * 60
    fake.responses = [fake.ok([("stocks", "fresh", 2 * day), ("stocks", "stale", 164 * day)])]
    with caplog.at_level(logging.INFO):
        posts = rf.collect_reddit_posts()
    assert [p.post_id for p in posts] == ["fresh"]
    assert any("2 unique posts, 1 from the last 7 days" in r.message for r in caplog.records)
