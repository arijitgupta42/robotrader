"""
Universe price fetching and cleaning.

One batched download of daily bars for every Universe stock (FTSE 350 and
S&P 500), cut off for each market at its last *completed* trading day before a
given timestamp (London for LSE stocks, New York for US stocks), with two data
problems handled:

* Yahoo sometimes switches an LSE stock between pence and pounds part-way
  through the window (BCG.L: 200.86 → 2.02 on 2026-08-24).  Those breaks are
  rescaled.  (US stocks are quoted in dollars only, so this never applies.)
* A single-day move above 40% that remains (e.g. ROR.L +68% on 2026-07-16)
  makes the swing indicators untrustworthy, so the stock is flagged as a
  Price Anomaly.  It stays in the data but cannot be a Pick.

The weekly run and the backfill both call fetch_universe_prices(), so their
snapshots are built identically.  Only download_batch() is tied to yfinance —
pass another `downloader` to fetch_universe_prices() to swap the data source.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .universe import MARKETS, market_of, universe_tickers

logger = logging.getLogger(__name__)

LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")

# The LSE session ends at 16:30 with a closing auction to 16:35, and Yahoo's
# daily bar is not reliably final straight after that, so a London day only
# counts as completed from 17:00.  The NYSE/Nasdaq session ends at 16:00 New
# York time; the same 17:00 rule applies there.
SESSION_COMPLETE = time(17, 0)
MARKET_TIMEZONES = {"LSE": LONDON, "US": NEW_YORK}

LOOKBACK_DAYS = 180           # calendar days of history (~6 months of daily bars)
MIN_BARS = 60                 # SMA(50) plus a margin for the other indicators
PRICE_ANOMALY_MOVE = 0.40     # single-day |close change| above this → Price Anomaly
STALE_DAYS = 3                # last bar older than this (calendar days) before the expected one → stale
UNIT_RATIO = (80.0, 125.0)    # a close-to-close ratio in this band, or its inverse, is a pence/pounds switch
REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
PRICE_COLUMNS = ["Open", "High", "Low", "Close"]


# ---------------------------------------------------------------------------
# Cutoff
# ---------------------------------------------------------------------------

def cutoff_date(as_of: datetime, market: str = "LSE") -> date:
    """
    The last calendar date whose trading session in `market` ('LSE' by default,
    or 'US') is complete at `as_of`.

    Weekends and bank holidays need no special handling: the data simply has
    no bar on those dates, so "bars up to this date" ends on the last real
    trading day.  A naive `as_of` is taken to be UTC.

    >>> cutoff_date(datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc))   # Monday, market open
    datetime.date(2026, 10, 4)
    >>> cutoff_date(datetime(2026, 10, 5, 17, 30, tzinfo=timezone.utc))  # Monday, after close
    datetime.date(2026, 10, 5)
    """
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    local = as_of.astimezone(MARKET_TIMEZONES[market])
    return local.date() if local.time() >= SESSION_COMPLETE else local.date() - timedelta(days=1)


def _latest_weekday(on_or_before: date) -> date:
    d = on_or_before
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

def fix_unit_breaks(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    Rescale pence/pounds switches inside a price series.

    A switch shows up as consecutive closes whose ratio is about 100 or about
    0.01.  The series is split at each switch and every segment is rescaled to
    the scale of the longest segment (the most recent one on a tie), which for
    LSE stocks is normally pence.  A one-bar spike of ×100 is two switches in
    a row, so it is corrected too.  Volume is untouched.

    Only switches *inside* the window can be seen: if Yahoo quotes the whole
    window in pounds, nothing is detected.

    Returns
    -------
    (DataFrame, int)
        The corrected frame and the number of switches found.
    """
    close = df["Close"]
    ratio = close / close.shift(1)
    lo, hi = UNIT_RATIO
    steps = pd.Series(0, index=df.index)
    steps[(ratio >= lo) & (ratio <= hi)] = 2          # price jumped ×100
    steps[(ratio <= 1 / lo) & (ratio >= 1 / hi)] = -2  # price fell to 1/100
    n_breaks = int((steps != 0).sum())
    if n_breaks == 0:
        return df, 0

    level = steps.cumsum()                      # segment values look 10**level times the first segment's
    position = pd.Series(np.arange(len(level)), index=level.index)
    counts = level.value_counts()
    reference = max(counts.index, key=lambda lv: (counts[lv], position[level == lv].max()))
    factor = 10.0 ** (reference - level)

    fixed = df.copy()
    fixed[PRICE_COLUMNS] = fixed[PRICE_COLUMNS].mul(factor, axis=0)
    return fixed, n_breaks


def max_abs_daily_move(df: pd.DataFrame) -> float:
    """Largest single-day absolute close-to-close change, as a fraction (0.4 = 40%)."""
    moves = df["Close"].pct_change().abs()
    return float(moves.max()) if len(moves) > 1 else 0.0


@dataclass
class PriceFetchResult:
    """Cleaned prices plus everything the weekly email and snapshot need to report."""

    cutoff_date: date                                                 # the LSE cutoff (the date shown in the email header)
    requested: list[str] = field(default_factory=list)
    cutoffs: dict[str, date] = field(default_factory=dict)            # market → cutoff; empty means cutoff_date for all
    data: dict[str, pd.DataFrame] = field(default_factory=dict)       # usable OHLCV frames
    no_data: list[str] = field(default_factory=list)                  # nothing (or malformed) came back
    short_history: dict[str, int] = field(default_factory=dict)       # ticker → bars, below MIN_BARS
    stale: dict[str, date] = field(default_factory=dict)              # ticker → last bar, far behind the cutoff
    unit_fixed: dict[str, int] = field(default_factory=dict)          # ticker → pence/pounds switches rescaled
    price_anomalies: dict[str, float] = field(default_factory=dict)   # ticker → max single-day move
    last_bar: dict[str, date] = field(default_factory=dict)           # ticker → date of the latest bar used

    def cutoff_for(self, ticker: str) -> date:
        """The cutoff that applies to a ticker's market."""
        return self.cutoffs.get(market_of(ticker), self.cutoff_date)

    @property
    def coverage(self) -> float:
        """Share of requested tickers that came back with usable data."""
        return len(self.data) / len(self.requested) if self.requested else 0.0


def clean_prices(raw: dict[str, pd.DataFrame], cutoff, requested: Optional[list[str]] = None) -> PriceFetchResult:
    """
    Turn raw per-ticker download frames into a PriceFetchResult.

    `cutoff` is one date for every stock, or a {market: date} dict.

    Drops rows with no close (the batch download shares one date index across
    tickers), removes bars after the stock's cutoff, then applies the unit fix
    (LSE stocks only) and the Price Anomaly flag.  Tickers with no data, too little history or a stale
    last bar are reported; short-history and no-data tickers are left out of
    `data`.  Stale and anomalous tickers stay in `data` so they still appear
    in the snapshot — the caller decides what to exclude.
    """
    requested = list(requested) if requested is not None else sorted(raw)
    cutoffs = dict(cutoff) if isinstance(cutoff, dict) else {m: cutoff for m in MARKETS}
    result = PriceFetchResult(cutoff_date=cutoffs["LSE"], requested=requested, cutoffs=cutoffs)

    for ticker in requested:
        market = market_of(ticker)
        cutoff = cutoffs[market]
        expected_last = _latest_weekday(cutoff)
        df = raw.get(ticker)
        if df is None or df.empty:
            result.no_data.append(ticker)
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df = df.copy()
            df.columns = df.columns.get_level_values(-1)
        if not set(REQUIRED_COLUMNS).issubset(df.columns):
            logger.warning("%s: unexpected columns %s", ticker, list(df.columns))
            result.no_data.append(ticker)
            continue

        df = df[list(REQUIRED_COLUMNS)].dropna(subset=["Close"]).sort_index()
        df = df[~df.index.duplicated(keep="first")]
        index = pd.DatetimeIndex(df.index)
        if index.tz is not None:
            index = index.tz_localize(None)
        df.index = index.normalize()
        df = df[df.index <= pd.Timestamp(cutoff)]
        if df.empty:
            result.no_data.append(ticker)
            continue
        if len(df) < MIN_BARS:
            result.short_history[ticker] = len(df)
            continue

        df, n_breaks = fix_unit_breaks(df) if market == "LSE" else (df, 0)
        if n_breaks:
            result.unit_fixed[ticker] = n_breaks
        move = max_abs_daily_move(df)
        if move > PRICE_ANOMALY_MOVE:
            result.price_anomalies[ticker] = round(move, 4)

        df["Volume"] = df["Volume"].fillna(0).astype(np.int64)
        df.index.name = "Date"
        last = df.index[-1].date()
        result.last_bar[ticker] = last
        if (expected_last - last).days > STALE_DAYS:
            result.stale[ticker] = last
        result.data[ticker] = df

    logger.info(
        "Prices to %s: %d/%d usable | no data %d, short %d, stale %d, unit-fixed %d, anomalies %d",
        ", ".join(f"{m} {d}" for m, d in cutoffs.items()), len(result.data), len(requested), len(result.no_data), len(result.short_history),
        len(result.stale), len(result.unit_fixed), len(result.price_anomalies),
    )
    return result


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_batch(tickers: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    """
    Download daily OHLCV bars for all tickers in one yfinance call.

    `end` is exclusive.  Prices are split/dividend adjusted.  Tickers Yahoo
    returns nothing for are simply absent from the result.  yfinance's own
    `repair` option is not used (it needs scipy, which would push the Lambda
    package over its size limit).
    """
    import yfinance as yf

    frame = yf.download(
        tickers     = tickers,
        start       = start.isoformat(),
        end         = end.isoformat(),
        interval    = "1d",
        auto_adjust = True,
        group_by    = "ticker",
        progress    = False,
        threads     = True,
    )
    if frame is None or frame.empty:
        return {}
    if not isinstance(frame.columns, pd.MultiIndex):          # a single ticker comes back flat
        return {tickers[0]: frame}
    available = set(frame.columns.get_level_values(0))
    return {t: frame[t] for t in tickers if t in available}


def fetch_universe_prices(
        as_of:         datetime,
        tickers:       Optional[list[str]] = None,
        lookback_days: int = LOOKBACK_DAYS,
        downloader:    Optional[Callable[[list[str], date, date], dict[str, pd.DataFrame]]] = None,
        ) -> PriceFetchResult:
    """
    Prices for the Universe as they stood at the last completed trading day
    before `as_of` (normally the Sector Signal's timestamp).

    Parameters
    ----------
    as_of : datetime
        Timestamp the data must predate.  Naive values are taken as UTC.
    tickers : list[str], optional
        Defaults to the whole Universe (FTSE 350).
    lookback_days : int
        Calendar days of history before the cutoff.
    downloader : callable, optional
        (tickers, start, end_exclusive) → {ticker: OHLCV frame}.  Defaults to
        download_batch(); inject another to change data source or to test.
    """
    tickers = list(tickers) if tickers is not None else universe_tickers()
    cutoffs = {m: cutoff_date(as_of, m) for m in MARKETS}
    start = min(cutoffs.values()) - timedelta(days=lookback_days)
    end = max(cutoffs.values()) + timedelta(days=1)

    logger.info("Downloading %d tickers, %s → %s (cutoffs %s)", len(tickers), start, end - timedelta(days=1), cutoffs)
    raw = (downloader or download_batch)(tickers, start, end)
    return clean_prices(raw, cutoffs, tickers)
