"""
reddit_fetcher.py — Pulls posts from finance/trading subreddits via RSS.

Uses Reddit's public RSS endpoints (/r/{sub}/{sort}.rss), which are served
through Reddit's CDN and are not subject to the IP-based blocking that their
JSON API applies to AWS Lambda egress addresses.

No authentication or API key required. Reddit's Responsible Builder Policy
(introduced late 2024) removed self-service OAuth app creation, making the
RSS approach the simplest viable path for read-only data collection.

Rate limit (measured 2026-10-31): unauthenticated RSS allows about ONE request
per minute per IP.  Every response, 200 or 429, carries x-ratelimit-remaining
and x-ratelimit-reset (seconds until the window reopens), so the fetcher waits
exactly that long instead of guessing, and retries a throttled request once.
To get the most out of each request:
  - ?limit=100 returns up to 100 posts (the default is 25)
  - r/a+b+c/hot.rss serves several subreddits in one request
REDDIT_REQUESTS in config.py lists the requests, highest value first; a time
budget stops the pass before it can run into the Lambda timeout.

Limitations vs the JSON API:
  - No comment fetching (RSS does not expose comment threads)
  - Scores and upvote ratios are not available in RSS; score is set to 0 and
    min_score filtering is skipped — the LLM pass filters quality instead

Parsed with feedparser, which is already a dependency of news_fetcher.py.

Failure modes (all logged as warnings, never silent):
  - HTTP 429       → wait for x-ratelimit-reset, retry once, then give up on that request
  - Other HTTP 4xx/5xx, timeouts, connection errors → give up on that request
  - Empty feed     → logged at DEBUG (some subs post infrequently)
A summary line "Reddit: N of M requests returned posts" ends every pass.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from config import REDDIT_REQUESTS, REDDIT_CFG

logger = logging.getLogger(__name__)

# Reddit's CDN serves RSS with a browser-like UA more reliably than a
# bot-identified string, even for public unauthenticated feeds.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SectorScout/1.0; "
        "+https://github.com/your-org/sector-scout)"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# RSS base — {subreddits} is one name or several joined with "+".
_RSS_BASE = "https://www.reddit.com/r/{subreddits}/{sort}.rss"

_REQUEST_TIMEOUT_S = 20
_SUBREDDIT_IN_LINK = re.compile(r"/r/([^/]+)/", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class RedditPost:
    subreddit:    str
    title:        str
    selftext:     str           # post body excerpt from RSS <content> field
    score:        int           # always 0 — not available in RSS
    upvote_ratio: float         # always 0.0 — not available in RSS
    num_comments: int           # always 0 — not available in RSS
    url:          str           # link to the post or external URL
    permalink:    str           # canonical reddit.com link
    post_id:      str           # extracted from RSS <id> field
    flair:        str           # not available in RSS; always ""
    created_utc:  datetime
    top_comments: List[str] = field(default_factory=list)  # always [] via RSS
    uid: str = field(init=False)

    def __post_init__(self):
        self.uid = hashlib.md5(self.post_id.encode()).hexdigest()

    def to_text(self) -> str:
        """
        Compact representation for the LLM prompt.
        Title + body excerpt; comments omitted (not available via RSS).
        """
        parts = [f"[r/{self.subreddit}] {self.title}"]

        body = self.selftext.strip()
        if body and body not in ("[removed]", "[deleted]"):
            parts.append(body[:400])

        return "\n".join(parts)


@dataclass
class _Response:
    status:    int                   # 0 = no HTTP response (timeout, DNS, connection error)
    body:      bytes = b""
    remaining: Optional[float] = None   # x-ratelimit-remaining
    reset_s:   Optional[float] = None   # x-ratelimit-reset: seconds until the window reopens
    error:     str = ""


# ---------------------------------------------------------------------------
# HTTP and clock (module-level so tests can replace them)
# ---------------------------------------------------------------------------

def _header_float(headers, name: str) -> Optional[float]:
    try:
        return float(headers.get(name))
    except (TypeError, ValueError):
        return None


def _http_get(url: str) -> _Response:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT_S)
    except requests.RequestException as exc:
        return _Response(status=0, error=str(exc))
    return _Response(
        status    = r.status_code,
        body      = r.content,
        remaining = _header_float(r.headers, "x-ratelimit-remaining"),
        reset_s   = _header_float(r.headers, "x-ratelimit-reset"),
    )


def _pause(seconds: float, stop: Optional[threading.Event]) -> bool:
    """Sleep ``seconds``; return True if ``stop`` was set meanwhile (abandon the pass)."""
    if stop is None:
        time.sleep(seconds)
        return False
    return stop.wait(seconds)


_clock = time.monotonic


# ---------------------------------------------------------------------------
# Post ID extraction
# ---------------------------------------------------------------------------

def _extract_post_id(entry) -> str:
    """
    Extract a stable post ID from an RSS entry.
    Reddit's RSS <id> field looks like:
      t3_abc123   (via <id> tag)
      https://www.reddit.com/r/sub/comments/abc123/title/  (via <link>)
    We try both and fall back to hashing the title.
    """
    # Try the <id> tag first — feedparser maps this to entry.id
    entry_id = getattr(entry, "id", "") or ""
    if entry_id.startswith("t3_"):
        return entry_id[3:]   # strip the "t3_" prefix
    if "t3_" in entry_id:
        part = entry_id.split("t3_")[-1]
        return part.split("/")[0] or entry_id

    # Fall back to extracting from the permalink URL
    link = getattr(entry, "link", "") or ""
    # URL pattern: /r/{sub}/comments/{post_id}/{title}/
    parts = [p for p in link.split("/") if p]
    try:
        comments_idx = parts.index("comments")
        return parts[comments_idx + 1]
    except (ValueError, IndexError):
        pass

    # Last resort: hash the title so deduplication still works
    title = getattr(entry, "title", "") or ""
    return hashlib.md5(title.encode()).hexdigest()[:8]


def _parse_date(entry) -> datetime:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# RSS parsing
# ---------------------------------------------------------------------------

def _parse_posts(body: bytes, requested: List[str]) -> List[RedditPost]:
    """Turn an RSS body into posts.  A combined feed mixes subreddits, so each
    post's subreddit is read from its link (falling back to the only requested one)."""
    feed = feedparser.parse(body)
    if feed.bozo and feed.bozo_exception:
        # Malformed XML is common in RSS; feedparser still extracts entries.
        logger.debug("Malformed XML in Reddit RSS — continuing anyway: %s", feed.bozo_exception)

    posts: List[RedditPost] = []
    for entry in feed.entries:
        title = getattr(entry, "title", "").strip()
        if not title:
            continue

        # RSS body is in entry.summary or entry.content — may contain HTML
        raw_body = ""
        if hasattr(entry, "content") and entry.content:
            raw_body = entry.content[0].get("value", "")
        elif hasattr(entry, "summary"):
            raw_body = entry.summary or ""

        # Strip HTML tags from body
        if raw_body:
            raw_body = BeautifulSoup(raw_body, "html.parser").get_text(" ", strip=True)

        link = getattr(entry, "link", "").strip()
        match = _SUBREDDIT_IN_LINK.search(link)
        subreddit = match.group(1) if match else (requested[0] if len(requested) == 1 else "reddit")

        posts.append(RedditPost(
            subreddit    = subreddit,
            title        = title,
            selftext     = raw_body[:500],
            score        = 0,       # not in RSS
            upvote_ratio = 0.0,     # not in RSS
            num_comments = 0,       # not in RSS
            url          = link,
            permalink    = link,
            post_id      = _extract_post_id(entry),
            flair        = "",      # not in RSS
            created_utc  = _parse_date(entry),
        ))
    return posts


def _request_url(request: Dict) -> str:
    url = _RSS_BASE.format(subreddits="+".join(request["subreddits"]), sort=request.get("sort", "hot"))
    params = [f"limit={request.get('limit', REDDIT_CFG.posts_per_request)}"]
    if request.get("t"):
        params.append(f"t={request['t']}")      # time window for sort=top: day|week|month|...
    return f"{url}?{'&'.join(params)}"


def _gap_after(resp: _Response) -> float:
    """Seconds to wait before the next request, from Reddit's own rate-limit headers."""
    if resp.remaining is not None and resp.reset_s is not None:
        if resp.remaining >= 1:
            return REDDIT_CFG.min_gap_s
        return min(resp.reset_s + REDDIT_CFG.reset_margin_s, REDDIT_CFG.max_wait_s)
    return REDDIT_CFG.fallback_gap_s            # headers missing: assume the measured limit


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def cap_balanced(posts: List[RedditPost], max_total: int) -> List[RedditPost]:
    """
    Keep at most ``max_total`` posts with every subreddit getting a fair share:
    subreddits take turns contributing their newest remaining post, so a busy
    one (r/wallstreetbets) cannot crowd out a quiet one (r/UKInvesting).
    The result is newest-first.
    """
    if len(posts) <= max_total:
        return sorted(posts, key=lambda p: p.created_utc, reverse=True)

    queues: Dict[str, List[RedditPost]] = {}
    for p in sorted(posts, key=lambda p: p.created_utc, reverse=True):
        queues.setdefault(p.subreddit, []).append(p)

    kept: List[RedditPost] = []
    turn = 0
    while len(kept) < max_total:
        for queue in queues.values():
            if turn < len(queue) and len(kept) < max_total:
                kept.append(queue[turn])
        turn += 1

    kept.sort(key=lambda p: p.created_utc, reverse=True)
    return kept


def collect_reddit_posts(
    max_total: Optional[int] = None,
    stop: Optional[threading.Event] = None,
) -> List[RedditPost]:
    """
    Runs every request in REDDIT_REQUESTS, in order, paced by Reddit's
    rate-limit headers.  Deduplicates by post_id and caps at max_total with a
    fair share per subreddit.  Stops starting requests once REDDIT_CFG.time_budget_s
    has passed, or as soon as ``stop`` is set (the caller no longer needs the posts).

    Note: min_score filtering is intentionally skipped here because RSS feeds
    do not expose scores.  Quality filtering is delegated entirely to the LLM
    pass in reddit_analyzer.py.
    """
    max_total  = max_total or REDDIT_CFG.max_posts_per_cycle
    all_posts: List[RedditPost] = []
    seen_ids:  set = set()
    started    = _clock()
    succeeded  = 0
    wait_s     = 0.0

    logger.info("--- Fetching Reddit posts (RSS, no auth, %d requests) ---", len(REDDIT_REQUESTS))

    for done, request in enumerate(REDDIT_REQUESTS):
        label = "+".join(request["subreddits"]) + f"/{request.get('sort', 'hot')}"

        if stop is not None and stop.is_set():
            break
        if _clock() - started + wait_s > REDDIT_CFG.time_budget_s:
            logger.warning(
                "Reddit: time budget of %.0f s reached — skipping the last %d of %d requests (starting at %s).",
                REDDIT_CFG.time_budget_s, len(REDDIT_REQUESTS) - done, len(REDDIT_REQUESTS), label,
            )
            break
        if wait_s > 0 and _pause(wait_s, stop):
            break

        posts, wait_s = _fetch_request(request, label, started, stop)
        if posts is None:
            if stop is not None and stop.is_set():
                break
            continue
        if posts:
            succeeded += 1
        for p in posts:
            if p.uid not in seen_ids:
                seen_ids.add(p.uid)
                all_posts.append(p)

    cutoff = datetime.now(timezone.utc) - timedelta(days=REDDIT_CFG.max_age_days)
    fresh = [p for p in all_posts if p.created_utc >= cutoff]
    kept = cap_balanced(fresh, max_total)
    logger.info(
        "Reddit: %d of %d requests returned posts — %d unique posts, %d from the last %d days, "
        "from %d subreddits (kept %d, cap %d) in %.0f s.",
        succeeded, len(REDDIT_REQUESTS), len(all_posts), len(fresh), REDDIT_CFG.max_age_days,
        len({p.subreddit for p in fresh}), len(kept), max_total, _clock() - started,
    )
    return kept


def _fetch_request(request: Dict, label: str, started: float, stop: Optional[threading.Event]):
    """
    One request, retried once on HTTP 429.  Returns (posts, seconds to wait
    before the next request); posts is None when the request failed.
    """
    url = _request_url(request)
    logger.info("  Fetching r/%s ...", label)
    resp = _http_get(url)

    if resp.status == 429:
        wait = _gap_after(resp)
        if _clock() - started + wait > REDDIT_CFG.time_budget_s:
            logger.warning("Reddit: r/%s throttled (HTTP 429) and no time left to retry — skipped.", label)
            return None, 0.0
        logger.warning("Reddit: r/%s throttled (HTTP 429) — retrying once in %.0f s.", label, wait)
        if _pause(wait, stop):
            return None, 0.0
        resp = _http_get(url)

    if resp.status != 200:
        why = resp.error or f"HTTP {resp.status}"
        logger.warning("Reddit: r/%s failed (%s) — skipped.", label, why)
        return None, _gap_after(resp) if resp.status else 0.0

    posts = _parse_posts(resp.body, request["subreddits"])
    if posts:
        logger.info("  SUCCESS r/%-40s %d posts", label, len(posts))
    else:
        logger.debug("r/%s: empty RSS feed.", label)
    return posts, _gap_after(resp)
