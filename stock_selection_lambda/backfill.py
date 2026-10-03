"""
Rebuild Universe Snapshots for past weeks from the scout's saved results.

    python stock_selection_lambda/backfill.py --bucket BUCKET [--region eu-west-1]
                                              [--out-dir backfill_out | --upload] [--overwrite]

For every ISO week with a successful scout run in  successful/  (the first
success of the week that has signals), it rebuilds the Universe Snapshot as of
the last completed trading day before the signal timestamp, with the same
price-cleaning, analysis and selection code as the weekly Lambda, and marks
every row `backfilled = True`.  No emails are sent.

By default snapshots are written to --out-dir (nothing touches S3 except
reads).  With --upload they go to  snapshots/YYYY-Www/universe.csv  in the
bucket; weeks that already have a snapshot are skipped unless --overwrite.

Prices are downloaded once for the whole span and sliced per week, so each
week sees exactly the bars the Lambda would have seen on that day.

Needs boto3 (and botocore[crt] if you use `aws login` credentials) plus the
packages in requirements.txt.
"""
import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lse_stock_analysis.prices import LOOKBACK_DAYS, cutoff_date, download_batch, fetch_universe_prices  # noqa: E402
from lse_stock_analysis.selection import (  # noqa: E402
    analyse_universe, build_snapshot, select_stocks, snapshot_to_csv, week_label,
)
from lse_stock_analysis.universe import universe_tickers  # noqa: E402

logger = logging.getLogger("backfill")

SUCCESS_KEY = re.compile(r"^successful/\d{4}-W\d{2}/response_[^/]+\.json$")
MIN_COVERAGE = 0.80                        # same threshold as the weekly Lambda
UNKNOWN_CONVERGENCE = "Unknown"            # results from before the scout recorded convergence_type

SURVIVORSHIP_NOTE = (
    "Survivorship bias: the Universe is today's FTSE 350 (the Sector Map), so stocks that left the "
    "index since a backfilled week are missing from it, and stocks that joined later appear in weeks "
    "when they were not yet in the index. Prices are adjusted as Yahoo reports them today."
)


@dataclass
class WeekPlan:
    week: str                 # label of the signal timestamp, same as the weekly Lambda uses
    source_key: str
    signal_ts: datetime
    signals: list[dict]
    keyword_fallback: bool    # signals came from the scout's keyword heuristic, not the LLM


def signal_time(payload: dict) -> datetime:
    """Earliest signal timestamp, else the result's own (same rule as the weekly Lambda)."""
    stamps = []
    for s in payload.get("signals") or []:
        try:
            ts = datetime.fromisoformat(str(s["timestamp"]).replace("Z", "+00:00"))
            stamps.append(ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc))
        except (KeyError, ValueError):
            continue
    if stamps:
        return min(stamps)
    return datetime.strptime(payload["timestamp"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def normalise_signals(signals: list[dict]) -> list[dict]:
    """Results saved before convergence_type existed get "Unknown", which applies no pick cap."""
    return [s if "convergence_type" in s else {**s, "convergence_type": UNKNOWN_CONVERGENCE} for s in signals]


def is_keyword_fallback(signals: list[dict]) -> bool:
    return bool(signals) and all("heuristic" in str(s.get("propagation", "")).lower() for s in signals)


def plan_weeks(keys: list[str], load: Callable[[str], dict]) -> list[WeekPlan]:
    """The first successful result with usable signals for each ISO week, oldest first."""
    plans: dict[str, WeekPlan] = {}
    for key in sorted(k for k in keys if SUCCESS_KEY.match(k)):        # keys embed a UTC timestamp, so they sort by time
        try:
            payload = load(key)
            signals = [s for s in (payload.get("signals") or []) if "sector" in s and "confidence" in s]
            if not payload.get("success", True) or not signals:
                continue
            ts = signal_time(payload)
        except Exception as exc:
            logger.warning("Skipping %s: %s: %s", key, type(exc).__name__, exc)
            continue
        week = week_label(ts)
        if week not in plans:
            plans[week] = WeekPlan(week, key, ts, normalise_signals(signals), is_keyword_fallback(signals))
    return [plans[w] for w in sorted(plans)]


class SlicingDownloader:
    """Downloads once for the whole span, then answers each week's request from the cached frames."""

    def __init__(self, start: date, end: date, download: Callable = download_batch):
        self.start, self.end, self.download = start, end, download
        self.frames: Optional[dict[str, pd.DataFrame]] = None

    def __call__(self, tickers: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
        if self.frames is None:
            logger.info("Downloading %d tickers, %s -> %s (once for every week)", len(tickers), self.start, self.end)
            self.frames = self.download(tickers, self.start, self.end)
        out = {}
        for t, df in self.frames.items():
            days = pd.Index(df.index).tz_localize(None).normalize() if getattr(df.index, "tz", None) else pd.Index(df.index).normalize()
            keep = (days >= pd.Timestamp(start)) & (days < pd.Timestamp(end))
            if keep.any():
                out[t] = df[keep]
        return out


def download_span(plans: list[WeekPlan]) -> tuple[date, date]:
    """Enough history to cover the lookback before the earliest week, through the latest cutoff."""
    cutoffs = [cutoff_date(p.signal_ts) for p in plans]
    return min(cutoffs) - timedelta(days=LOOKBACK_DAYS), max(cutoffs) + timedelta(days=1)


def build_week(plan: WeekPlan, downloader: Callable, tickers: Optional[list[str]] = None) -> Optional[pd.DataFrame]:
    """The backfilled snapshot for one week, or None if too few stocks returned prices."""
    prices = fetch_universe_prices(plan.signal_ts, tickers=tickers, downloader=downloader)
    if prices.coverage < MIN_COVERAGE:
        logger.warning("%s: only %d of %d stocks have prices (%.0f%%); skipped",
                       plan.week, len(prices.data), len(prices.requested), prices.coverage * 100)
        return None
    analysis = analyse_universe(prices.data)
    selections = select_stocks(plan.signals, analysis, prices)
    return build_snapshot(plan.signal_ts, plan.signals, analysis, prices, selections, backfilled=True)


def backfill(plans: list[WeekPlan], write: Callable[[str, str], None], exists: Callable[[str], bool],
             overwrite: bool = False, download: Callable = download_batch, tickers: Optional[list[str]] = None) -> dict:
    """Build and write a snapshot for each planned week.  Returns {"written", "skipped", "failed"} lists of weeks."""
    result = {"written": [], "skipped": [], "failed": []}
    todo = []
    for p in plans:
        if not overwrite and exists(p.week):
            logger.info("%s: snapshot already exists; skipped (use --overwrite to replace)", p.week)
            result["skipped"].append(p.week)
        else:
            todo.append(p)
    if not todo:
        return result
    start, end = download_span(todo)
    downloader = SlicingDownloader(start, end, download)
    for p in todo:
        try:
            snapshot = build_week(p, downloader, tickers)
        except Exception as exc:
            logger.error("%s: failed: %s: %s", p.week, type(exc).__name__, exc)
            snapshot = None
        if snapshot is None:
            result["failed"].append(p.week)
            continue
        write(p.week, snapshot_to_csv(snapshot))
        result["written"].append(p.week)
        logger.info("%s: written (%d rows, %d picks)", p.week, len(snapshot), int(snapshot["is_pick"].sum()))
    return result


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bucket", required=True, help="the scout's S3 bucket (read successful/, optionally write snapshots/)")
    parser.add_argument("--region", default="eu-west-1")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "backfill_out", help="where to write snapshots when not uploading")
    parser.add_argument("--upload", action="store_true", help="write to snapshots/ in the bucket instead of --out-dir")
    parser.add_argument("--overwrite", action="store_true", help="replace snapshots that already exist")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import boto3
    from botocore.exceptions import ClientError
    s3 = boto3.client("s3", region_name=args.region)

    keys = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=args.bucket, Prefix="successful/")
            for o in page.get("Contents", [])]
    plans = plan_weeks(keys, lambda k: json.loads(s3.get_object(Bucket=args.bucket, Key=k)["Body"].read()))
    if not plans:
        print("No successful scout results found.")
        return 1

    def snapshot_key(week: str) -> str:
        return f"snapshots/{week}/universe.csv"

    if args.upload:
        def exists(week: str) -> bool:
            try:
                s3.head_object(Bucket=args.bucket, Key=snapshot_key(week))
                return True
            except ClientError as exc:
                if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise

        def write(week: str, text: str) -> None:
            s3.put_object(Bucket=args.bucket, Key=snapshot_key(week), Body=text.encode("utf-8"), ContentType="text/csv")
    else:
        def exists(week: str) -> bool:
            return (args.out_dir / snapshot_key(week)).exists()

        def write(week: str, text: str) -> None:
            path = args.out_dir / snapshot_key(week)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="")

    result = backfill(plans, write, exists, overwrite=args.overwrite)

    where = f"s3://{args.bucket}/snapshots/" if args.upload else str(args.out_dir / "snapshots")
    print(f"\nBackfill finished -> {where}")
    print(f"  written: {len(result['written'])}  skipped (already there): {len(result['skipped'])}  failed: {len(result['failed'])}")
    if result["failed"]:
        print("  failed weeks:", ", ".join(result["failed"]))
    fallback = [p.week for p in plans if p.keyword_fallback and p.week in result["written"]]
    if fallback:
        print("  keyword-fallback signals (not LLM output; consider excluding from backtests):", ", ".join(fallback))
    unknown = [p.week for p in plans if p.week in result["written"]
               and any(s["convergence_type"] == UNKNOWN_CONVERGENCE for s in p.signals)]
    if unknown:
        print("  convergence_type was not recorded (shown as 'Unknown', no pick cap applied):", ", ".join(unknown))
    print("\nNOTE:", SURVIVORSHIP_NOTE)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
