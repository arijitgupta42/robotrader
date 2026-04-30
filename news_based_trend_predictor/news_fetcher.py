"""
news_fetcher.py — Pulls headlines from RSS feeds and HTML scrape targets.

Hardened against the two most common failure modes:
  1. DNS / network errors  ("Name or service not known")
     → Feed is skipped silently after one failure; its health state is
       tracked so repeated failures are only logged at DEBUG level.
  2. Malformed XML         ("not well-formed (invalid token)")
     → feedparser's bozo flag is expected for many financial RSS feeds
       that include unescaped ampersands.  We demote this to DEBUG and
       continue — feedparser still extracts entries from broken feeds.
"""

import hashlib
import logging
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

from config import RSS_FEEDS, SCRAPE_TARGETS, SCHEDULER_CFG

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Headline:
    source:    str
    title:     str
    summary:   str
    url:       str
    published: datetime
    uid:       str = field(init=False)

    def __post_init__(self):
        self.uid = hashlib.md5(self.title.strip().lower().encode()).hexdigest()

    def to_text(self) -> str:
        summary_part = f" — {self.summary[:200]}" if self.summary else ""
        return f"[{self.source}] {self.title}{summary_part}"


# ---------------------------------------------------------------------------
# Feed health tracker
# ---------------------------------------------------------------------------

class _FeedHealth:
    """
    Tracks consecutive failure counts per feed URL.
    After MAX_CONSECUTIVE_FAILURES we stop logging at WARNING and drop to
    DEBUG to avoid log spam on permanently dead feeds.
    """
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self):
        self._failures: Dict[str, int] = {}

    def record_success(self, url: str) -> None:
        self._failures[url] = 0

    def record_failure(self, url: str) -> None:
        self._failures[url] = self._failures.get(url, 0) + 1

    def failure_count(self, url: str) -> int:
        return self._failures.get(url, 0)

    def log_level(self, url: str) -> int:
        """
        Return the appropriate logging level for a failure on this URL.
        First few failures → WARNING; repeated failures → DEBUG (suppress noise).
        """
        if self._failures.get(url, 0) < self.MAX_CONSECUTIVE_FAILURES:
            return logging.WARNING
        return logging.DEBUG


_health = _FeedHealth()

# ---------------------------------------------------------------------------
# Shared HTTP headers
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SectorScout/1.0; "
        "Python-requests; +https://github.com/your-org/sector-scout)"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# ---------------------------------------------------------------------------
# DNS pre-check helper
# ---------------------------------------------------------------------------

def _host_reachable(url: str, timeout: float = 3.0) -> bool:
    """
    Quick DNS lookup before attempting a full HTTP request.
    Returns False immediately if the hostname can't be resolved,
    saving several seconds of socket timeout per dead feed.
    """
    try:
        from urllib.parse import urlparse
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
# Date parsing
# ---------------------------------------------------------------------------

def _parse_rss_date(entry) -> datetime:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# RSS fetching
# ---------------------------------------------------------------------------

def fetch_rss(feed_cfg: dict, timeout: int = 10) -> List[Headline]:
    url  = feed_cfg["url"]
    name = feed_cfg["name"]

    # --- DNS pre-check ---
    if not _host_reachable(url):
        level = _health.log_level(url)
        logger.log(level, "DNS lookup failed for %s (%s) — skipping.", name, url)
        _health.record_failure(url)
        return []

    headlines: List[Headline] = []
    try:
        feed = feedparser.parse(url, request_headers=_HEADERS)

        # --- Malformed XML (bozo) ---
        # feedparser sets bozo=True for XML parse errors but still extracts
        # whatever entries it can.  We only log at DEBUG because it is
        # extremely common with financial RSS feeds (unescaped &, >, etc.)
        # and does NOT mean we got zero data.
        if feed.bozo and feed.bozo_exception:
            exc_msg = str(feed.bozo_exception)
            # Network errors ARE worth a warning; XML errors are noise
            is_network_error = any(
                kw in exc_msg.lower()
                for kw in ("name or service", "errno", "connection", "timeout", "refused")
            )
            if is_network_error:
                level = _health.log_level(url)
                logger.log(level, "Network error fetching %s: %s", name, exc_msg)
                _health.record_failure(url)
                return []
            else:
                # Malformed XML — log at DEBUG, continue parsing
                logger.debug("Malformed XML in %s (bozo): %s — continuing anyway.", name, exc_msg)

        entries_parsed = 0
        for entry in feed.entries:
            title   = getattr(entry, "title",   "").strip()
            summary = getattr(entry, "summary", "").strip()
            url_    = getattr(entry, "link",    "").strip()

            if not title:
                continue

            # Strip residual HTML tags from summary
            if summary:
                summary = BeautifulSoup(summary, "html.parser").get_text(" ", strip=True)

            headlines.append(Headline(
                source    = name,
                title     = title,
                summary   = summary[:500],
                url       = url_,
                published = _parse_rss_date(entry),
            ))
            entries_parsed += 1

        if entries_parsed > 0:
            _health.record_success(url)
            logger.info("  ✓ %-30s  %d headlines", name, entries_parsed)
        else:
            logger.debug("  ✗ %-30s  0 headlines (empty feed)", name)

    except Exception as exc:
        level = _health.log_level(url)
        logger.log(level, "Unexpected error fetching %s: %s", name, exc)
        _health.record_failure(url)

    return headlines


# ---------------------------------------------------------------------------
# HTML scraping fallback
# ---------------------------------------------------------------------------

_HEADLINE_TAGS = ["h1", "h2", "h3"]


def fetch_html_headlines(target: dict, timeout: int = 12) -> List[Headline]:
    url  = target["url"]
    name = target["name"]

    if not _host_reachable(url):
        level = _health.log_level(url)
        logger.log(level, "DNS lookup failed for scrape target %s — skipping.", name)
        _health.record_failure(url)
        return []

    headlines: List[Headline] = []
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
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

            sibling = tag.find_next_sibling(["p", "span"])
            summary = sibling.get_text(" ", strip=True)[:500] if sibling else ""

            parent_a = tag.find_parent("a")
            href     = parent_a.get("href", "") if parent_a else ""
            if href and not href.startswith("http"):
                href = urljoin(url, href)

            headlines.append(Headline(
                source    = name,
                title     = text,
                summary   = summary,
                url       = href,
                published = datetime.now(timezone.utc),
            ))

        if headlines:
            _health.record_success(url)
            logger.info("  ✓ %-30s  %d headlines (scraped)", name, len(headlines))
        else:
            logger.debug("  ✗ %-30s  0 headlines (scrape found nothing)", name)

    except requests.exceptions.ConnectionError as exc:
        level = _health.log_level(url)
        logger.log(level, "Connection error scraping %s: %s", name, exc)
        _health.record_failure(url)
    except requests.exceptions.Timeout:
        level = _health.log_level(url)
        logger.log(level, "Timeout scraping %s", name)
        _health.record_failure(url)
    except Exception as exc:
        level = _health.log_level(url)
        logger.log(level, "Unexpected error scraping %s: %s", name, exc)
        _health.record_failure(url)

    return headlines


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def collect_headlines(max_total: Optional[int] = None) -> List[Headline]:
    """
    Pulls from all configured RSS feeds and scrape targets.
    Deduplicates by title hash, sorts newest-first, caps at max_total.
    """
    max_total = max_total or SCHEDULER_CFG.max_headlines_per_cycle
    all_headlines: List[Headline] = []
    seen_uids: set = set()

    logger.info("--- Fetching RSS feeds ---")
    for feed_cfg in RSS_FEEDS:
        for h in fetch_rss(feed_cfg):
            if h.uid not in seen_uids:
                seen_uids.add(h.uid)
                all_headlines.append(h)
        time.sleep(0.4)   # polite crawl delay

    logger.info("--- Scraping HTML targets ---")
    for target in SCRAPE_TARGETS:
        for h in fetch_html_headlines(target):
            if h.uid not in seen_uids:
                seen_uids.add(h.uid)
                all_headlines.append(h)
        time.sleep(0.4)

    all_headlines.sort(key=lambda h: h.published, reverse=True)

    total = len(all_headlines)
    sources_active = len({h.source for h in all_headlines})
    logger.info(
        "Collection complete — %d unique headlines from %d sources (cap=%d).",
        total, sources_active, max_total,
    )

    if total == 0:
        logger.warning(
            "No headlines collected at all. "
            "Check your internet connection and DNS. "
            "Run: python -c \"import socket; socket.getaddrinfo('feeds.bbci.co.uk', None)\" "
            "to verify basic DNS resolution."
        )

    return all_headlines[:max_total]
