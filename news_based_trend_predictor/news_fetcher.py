"""
news_fetcher.py — Pulls headlines from RSS feeds and lightweight HTML scrapes.

Returns a flat list of Headline dataclasses, deduplicated by title hash.
No JavaScript rendering is used — targets static or server-rendered pages only,
which covers the majority of UK financial news outlets.
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from config import RSS_FEEDS, SCRAPE_TARGETS, SCHEDULER_CFG

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data Model
# ---------------------------------------------------------------------------

@dataclass
class Headline:
    source:    str
    title:     str
    summary:   str          # may be empty string
    url:       str
    published: datetime
    uid:       str = field(init=False)

    def __post_init__(self):
        # Stable dedup key — title hash (lowercased, stripped)
        self.uid = hashlib.md5(self.title.strip().lower().encode()).hexdigest()

    def to_text(self) -> str:
        """Single-line representation fed to the LLM prompt."""
        summary_part = f" — {self.summary[:200]}" if self.summary else ""
        return f"[{self.source}] {self.title}{summary_part}"


# ---------------------------------------------------------------------------
# RSS Fetching
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SectorScout/1.0; "
        "+https://github.com/your-org/sector-scout)"
    )
}

def _parse_rss_date(entry) -> datetime:
    """Best-effort datetime extraction from a feedparser entry."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def fetch_rss(feed_cfg: dict, timeout: int = 10) -> List[Headline]:
    headlines: List[Headline] = []
    try:
        # feedparser handles gzip, etag, etc. natively
        feed = feedparser.parse(feed_cfg["url"], request_headers=_HEADERS)
        if feed.bozo and feed.bozo_exception:
            logger.warning("RSS parse warning for %s: %s", feed_cfg["name"], feed.bozo_exception)

        for entry in feed.entries:
            title   = getattr(entry, "title",   "").strip()
            summary = getattr(entry, "summary", "").strip()
            url     = getattr(entry, "link",    "").strip()
            if not title:
                continue
            # Strip any residual HTML from summary
            summary = BeautifulSoup(summary, "html.parser").get_text(" ", strip=True)
            headlines.append(
                Headline(
                    source    = feed_cfg["name"],
                    title     = title,
                    summary   = summary[:500],
                    url       = url,
                    published = _parse_rss_date(entry),
                )
            )
    except Exception as exc:
        logger.error("Failed to fetch RSS %s: %s", feed_cfg["name"], exc)
    return headlines


# ---------------------------------------------------------------------------
# HTML Scraping (fallback / supplement for non-RSS sources)
# ---------------------------------------------------------------------------

_HEADLINE_TAGS = ["h1", "h2", "h3"]


def fetch_html_headlines(target: dict, timeout: int = 12) -> List[Headline]:
    headlines: List[Headline] = []
    try:
        resp = requests.get(target["url"], headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        seen: set = set()
        for tag in soup.find_all(_HEADLINE_TAGS):
            text = tag.get_text(" ", strip=True)
            if len(text) < 15 or len(text) > 300:
                continue
            if text in seen:
                continue
            seen.add(text)

            # Try to grab a sibling <p> or <span> as summary
            sibling = tag.find_next_sibling(["p", "span"])
            summary = sibling.get_text(" ", strip=True)[:500] if sibling else ""

            # Try to resolve a link
            parent_a = tag.find_parent("a")
            href     = parent_a.get("href", "") if parent_a else ""
            if href and not href.startswith("http"):
                from urllib.parse import urljoin
                href = urljoin(target["url"], href)

            headlines.append(
                Headline(
                    source    = target["name"],
                    title     = text,
                    summary   = summary,
                    url       = href,
                    published = datetime.now(timezone.utc),
                )
            )
    except Exception as exc:
        logger.error("Failed to scrape %s: %s", target["name"], exc)
    return headlines


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def collect_headlines(max_total: Optional[int] = None) -> List[Headline]:
    """
    Pulls from all configured RSS feeds and scrape targets.
    Deduplicates by title hash and returns at most `max_total` headlines,
    sorted newest-first.
    """
    max_total = max_total or SCHEDULER_CFG.max_headlines_per_cycle
    all_headlines: List[Headline] = []
    seen_uids: set = set()

    # --- RSS ---
    for feed_cfg in RSS_FEEDS:
        for h in fetch_rss(feed_cfg):
            if h.uid not in seen_uids:
                seen_uids.add(h.uid)
                all_headlines.append(h)
        # polite crawl delay
        time.sleep(0.5)

    # --- Scrape fallbacks ---
    for target in SCRAPE_TARGETS:
        for h in fetch_html_headlines(target):
            if h.uid not in seen_uids:
                seen_uids.add(h.uid)
                all_headlines.append(h)
        time.sleep(0.5)

    # Sort newest first, cap
    all_headlines.sort(key=lambda h: h.published, reverse=True)
    logger.info("Collected %d unique headlines (cap=%d)", len(all_headlines), max_total)
    return all_headlines[:max_total]
