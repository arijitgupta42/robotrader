"""
reddit_fetcher.py — Pulls posts from finance/trading subreddits via RSS.

Uses Reddit's public RSS endpoints (/r/{sub}.rss and /r/{sub}/top.rss) which
are served through Reddit's CDN and are not subject to the IP-based blocking
that their JSON API applies to AWS Lambda egress addresses.

No authentication or API key required. Reddit's Responsible Builder Policy
(introduced late 2024) removed self-service OAuth app creation, making the
RSS approach the simplest viable path for read-only data collection.

Limitations vs the JSON API:
  - 25 posts per feed (RSS hard limit — fetch both /hot and /top to compensate)
  - No comment fetching (RSS does not expose comment threads)
  - Scores and upvote ratios are not available in RSS; score is set to 0 and
    min_score filtering is skipped — the LLM pass filters quality instead

Parsed with feedparser, which is already a dependency of news_fetcher.py.

Failure modes handled:
  - DNS failure    → skip subreddit, log warning
  - HTTP 4xx/5xx  → skip subreddit, log warning
  - Malformed XML  → feedparser still extracts what it can (same as news feeds)
  - Empty feed     → logged at DEBUG, not WARNING (some subs post infrequently)
"""

from __future__ import annotations

import hashlib
import logging
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlparse

import feedparser
from bs4 import BeautifulSoup

from config import REDDIT_SUBREDDITS, REDDIT_CFG

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

# RSS base — sort is appended as a path segment: /r/{sub}/hot.rss etc.
_RSS_BASE = "https://www.reddit.com/r/{subreddit}/{sort}.rss"


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


# ---------------------------------------------------------------------------
# DNS helper
# ---------------------------------------------------------------------------

def _host_reachable(url: str, timeout: float = 3.0) -> bool:
    try:
        host = urlparse(url).hostname
        if not host:
            return False
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(host, None)
        return True
    except (socket.gaierror, socket.timeout, OSError):
        return False
    finally:
        socket.setdefaulttimeout(None)


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


# ---------------------------------------------------------------------------
# RSS fetcher
# ---------------------------------------------------------------------------

def fetch_subreddit_rss(subreddit: str, sort: str = "hot") -> List[RedditPost]:
    """
    Fetch up to 25 posts from a subreddit RSS feed.

    sort : "hot" | "top" | "new" | "rising"
           Reddit's RSS hard-limits all feeds to 25 entries regardless of sort.
           Fetching both "hot" and "top" for a subreddit gives ~50 unique posts
           (with deduplication handled in collect_reddit_posts).
    """
    url = _RSS_BASE.format(subreddit=subreddit, sort=sort)
    logger.info("  Fetching r/%s (%s) via RSS ...", subreddit, sort)

    if not _host_reachable(url):
        logger.warning("DNS lookup failed for %s — skipping r/%s.", url, subreddit)
        return []

    feed = feedparser.parse(url, request_headers=_HEADERS)

    # Malformed XML is common in RSS; feedparser still extracts entries.
    # Only bail out if it looks like a network error rather than XML noise.
    if feed.bozo and feed.bozo_exception:
        exc_msg = str(feed.bozo_exception)
        is_network = any(
            kw in exc_msg.lower()
            for kw in ("name or service", "errno", "connection", "timeout", "refused", "403", "404")
        )
        if is_network:
            logger.warning("Network error fetching r/%s RSS (%s): %s", subreddit, sort, exc_msg)
            return []
        logger.debug("Malformed XML in r/%s RSS (%s) — continuing anyway: %s", subreddit, sort, exc_msg)

    if not feed.entries:
        logger.debug("r/%s (%s): empty RSS feed.", subreddit, sort)
        return []

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

        link      = getattr(entry, "link", "").strip()
        post_id   = _extract_post_id(entry)
        published = _parse_date(entry)

        posts.append(RedditPost(
            subreddit    = subreddit,
            title        = title,
            selftext     = raw_body[:500],
            score        = 0,       # not in RSS
            upvote_ratio = 0.0,     # not in RSS
            num_comments = 0,       # not in RSS
            url          = link,
            permalink    = link,
            post_id      = post_id,
            flair        = "",      # not in RSS
            created_utc  = published,
        ))

    logger.info("  ✓ r/%-22s  %d posts (sort=%s, via RSS)", subreddit, len(posts), sort)
    return posts


def _parse_date(entry) -> datetime:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def collect_reddit_posts(max_total: Optional[int] = None) -> List[RedditPost]:
    """
    Pulls posts from all subreddits in REDDIT_SUBREDDITS via RSS.
    Deduplicates by post_id, sorts newest-first, caps at max_total.

    Note: min_score filtering from the config is intentionally skipped here
    because RSS feeds do not expose scores. Quality filtering is delegated
    entirely to the LLM pass in reddit_analyzer.py.
    """
    max_total  = max_total or REDDIT_CFG.max_posts_per_cycle
    all_posts: List[RedditPost] = []
    seen_ids:  set = set()

    logger.info("--- Fetching Reddit posts (RSS, no auth) ---")

    for cfg in REDDIT_SUBREDDITS:
        sub   = cfg["subreddit"]
        sorts = cfg.get("sorts", ["hot"])

        for sort in sorts:
            posts = fetch_subreddit_rss(subreddit=sub, sort=sort)
            for p in posts:
                if p.uid not in seen_ids:
                    seen_ids.add(p.uid)
                    all_posts.append(p)
            # Polite crawl delay — Reddit's CDN is tolerant but don't hammer it
            time.sleep(1.5)

    # Sort newest-first so the LLM sees the most recent posts at the top
    all_posts.sort(key=lambda p: p.created_utc, reverse=True)

    logger.info(
        "Reddit RSS collection complete — %d unique posts from %d subreddits (cap=%d).",
        len(all_posts),
        len({p.subreddit for p in all_posts}),
        max_total,
    )

    return all_posts[:max_total]
