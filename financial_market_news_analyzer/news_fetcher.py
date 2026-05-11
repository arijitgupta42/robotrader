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
from datetime import datetime, timezone, date, timedelta
from typing import Dict, List, Optional
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

from config import RSS_FEEDS, SCHEDULER_CFG

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
            logger.info("SUCCESS %-30s  %d headlines", name, entries_parsed)
        else:
            logger.debug("%-30s  0 headlines (empty feed)", name)

    except Exception as exc:
        level = _health.log_level(url)
        logger.log(level, "Unexpected error fetching %s: %s", name, exc)
        _health.record_failure(url)

    return headlines

# ---------------------------------------------------------------------------
# HL scraping
# ---------------------------------------------------------------------------
def fetch_hl_weekly_outlook() -> List[Headline]:
    """
    Fetches HL's 'Next week on the stock market' article, published each
    Friday for the coming week. The URL encodes the date of the Monday
    of that week, e.g. next-week-on-the-stock-market-04-05-2026.

    We derive the correct Monday by finding the next Monday from today,
    then try that URL. If it 404s (not yet published), we fall back to
    the most recently elapsed Monday.
    """
    def monday_url(d: date) -> str:
        return (
            f"https://www.hl.co.uk/shares/share-research/"
            f"next-week-on-the-stock-market-{d.strftime('%d-%m-%Y')}"
        )

    today = date.today()
    # days_until_monday: 0 if today is Monday, else days forward to next Monday
    days_ahead = (7 - today.weekday()) % 7 or 7
    next_monday = today + timedelta(days=days_ahead)
    last_monday = next_monday - timedelta(weeks=1)

    headlines: List[Headline] = []

    for target_monday in (next_monday, last_monday):
        url = monday_url(target_monday)
        if not _host_reachable(url):
            continue
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=12)
            if resp.status_code == 404:
                logger.debug("HL weekly outlook not yet live at %s", url)
                continue
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            # Extract the article body — all <p> tags inside the main content
            # Each analyst section becomes one Headline with the section text as summary
            article_sections = soup.find_all("p")
            full_text_parts = []
            for p in article_sections:
                text = p.get_text(" ", strip=True)
                if len(text) > 40:   # skip nav fragments and boilerplate
                    full_text_parts.append(text)

            if not full_text_parts:
                logger.debug("HL weekly outlook: no content parsed from %s", url)
                continue

            # Also extract the earnings calendar table rows as individual headlines
            for row in soup.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cells) == 2 and cells[0] and cells[1]:
                    headlines.append(Headline(
                        source    = "HL Weekly Outlook",
                        title     = f"{cells[0]}: {cells[1]}",
                        summary   = "",
                        url       = url,
                        published = datetime.now(timezone.utc),
                    ))

            # Add the analyst commentary paragraphs as a single headline each
            for part in full_text_parts[:10]:   # cap at 10 paragraphs
                headlines.append(Headline(
                    source    = "HL Weekly Outlook",
                    title     = part[:120],    # first 120 chars as title
                    summary   = part,
                    url       = url,
                    published = datetime.now(timezone.utc),
                ))

            if headlines:
                logger.info("HL Weekly Outlook          %d items from %s",
                            len(headlines), url)
                return headlines   # got a valid page, stop trying

        except requests.exceptions.RequestException as exc:
            logger.warning("Error fetching HL weekly outlook from %s: %s", url, exc)

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
            logger.info("%-30s  %d headlines (scraped)", name, len(headlines))
        else:
            logger.debug("%-30s  0 headlines (scrape found nothing)", name)

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

def collect_headlines(max_total: Optional[int] = None, max_age_days: int = 7) -> List[Headline]:
    """
    Pulls from all configured RSS feeds and scrape targets.
    Deduplicates by title hash, filters to the last ``max_age_days`` days,
    sorts newest-first, and caps at max_total.

    ``max_age_days`` defaults to 7 so only headlines from the past week are
    forwarded to the LLM.  HL Weekly Outlook items are always included because
    they are scraped with ``datetime.now()`` and are inherently current.
    """
    max_total = max_total or SCHEDULER_CFG.max_headlines_per_cycle
    cutoff    = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    all_headlines: List[Headline] = []
    seen_uids: set = set()

    logger.info("--- Fetching RSS feeds (cutoff: last %d days) ---", max_age_days)
    stale_count = 0
    for feed_cfg in RSS_FEEDS:
        for h in fetch_rss(feed_cfg):
            if h.uid in seen_uids:
                continue
            seen_uids.add(h.uid)
            if h.published < cutoff:
                stale_count += 1
                continue
            all_headlines.append(h)
        time.sleep(0.4)   # polite crawl delay

    all_headlines.sort(key=lambda h: h.published, reverse=True)

    total = len(all_headlines)
    sources_active = len({h.source for h in all_headlines})
    logger.info(
        "Collection complete — %d unique headlines from %d sources "
        "(%d stale discarded, cap=%d).",
        total, sources_active, stale_count, max_total,
    )

    if total == 0:
        logger.warning(
            "No headlines collected at all. "
            "Check your internet connection and DNS. "
            "Run: python -c \"import socket; socket.getaddrinfo('feeds.bbci.co.uk', None)\" "
            "to verify basic DNS resolution."
        )

    # HL Weekly Outlook is always current (scraped with datetime.now()) — no age filter needed
    logger.info("--- Fetching HL weekly outlook ---")
    for h in fetch_hl_weekly_outlook():
        if h.uid not in seen_uids:
            seen_uids.add(h.uid)
            all_headlines.append(h)

    return all_headlines[:max_total]
