"""
handler.py — AWS Lambda entry point for the weekly stock selection.

Triggered by an S3 ObjectCreated event when the sector scout writes a
successful result under  successful/YYYY-Www/response_<ts>.json  (see
docs/adr/0001-stock-selection-runs-as-separate-lambda.md).  For that result:

  1. reads the Sector Signals;
  2. downloads prices for the whole Universe as of the last completed London
     trading day before the signals' timestamp (retrying, since Yahoo can
     fail or return partial data);
  3. analyses every stock, picks stocks in each Signalled Sector by rule;
  4. writes the Universe Snapshot to  snapshots/YYYY-Www/universe.csv;
  5. sends ONE email through SES: the scout's sector report with each sector's
     picks inside its card (the scout itself no longer sends an email).

Failure handling
----------------
Any failure is caught here: a record is written to  failed/YYYY-Www/  and ONE
email is sent, and the function then returns normally.  If the scout's result
could be read, that email is still the full sector report with a failure notice
in place of the picks; otherwise it is a short failure message.  It never
re-raises, because Lambda would retry an S3-triggered invocation and every
retry would send another failure email.  So the Terraform for this function
must set  maximum_retry_attempts = 0  on its async invoke config; the retries
that matter (the Yahoo download) happen inside the function.

Environment variables
---------------------
  SES_SENDER      verified SES sender address
  SES_RECIPIENT   address that receives the report
  AWS_REGION      set by the Lambda runtime (SES region defaults to eu-west-1)
"""
import json
import logging
import os
import re
import threading
import time
import traceback
from datetime import date, datetime, timezone
from typing import Callable, Optional
from urllib.parse import unquote_plus

from lse_stock_analysis.prices import PriceFetchResult, fetch_universe_prices
from lse_stock_analysis.selection import (
    analyse_universe, build_snapshot, select_stocks, snapshot_to_csv, week_label,
)

from email_report import build_failure_email, build_report_email, sector_map_age_days

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SUCCESS_KEY = re.compile(r"^successful/\d{4}-W\d{2}/response_[^/]+\.json$")
MIN_COVERAGE = 0.80                 # share of the Universe that must return prices
FETCH_ATTEMPTS = 3
FETCH_BACKOFF_SECONDS = (20, 60)    # waits before attempt 2 and 3
MIN_TIME_FOR_RETRY_MS = 120_000     # don't start another download with less than this left
RESERVE_SECONDS = 90                # kept back after a download for the analysis, the snapshot and the email
MAX_ATTEMPT_SECONDS = 180           # one download attempt (normally about 45 s) is abandoned after this long
MIN_ATTEMPT_SECONDS = 20            # ...but is always given at least this long


class StockSelectionError(Exception):
    """A week's stock selection cannot be completed."""


def signal_time(payload: dict) -> datetime:
    """When the scout's signals were produced: the earliest signal timestamp, else the result's own."""
    stamps = []
    for s in payload.get("signals") or []:
        try:
            ts = datetime.fromisoformat(str(s["timestamp"]).replace("Z", "+00:00"))
            stamps.append(ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc))
        except (KeyError, ValueError):
            continue
    if stamps:
        return min(stamps)
    try:
        return datetime.strptime(payload["timestamp"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError, TypeError):
        raise StockSelectionError("scout result has no usable timestamp")


def _remaining_ms(context) -> float:
    getter = getattr(context, "get_remaining_time_in_millis", None)
    return getter() if getter else float("inf")


def _call_with_deadline(fn: Callable, arg, seconds: Optional[float]):
    """
    Call fn(arg), giving up after `seconds` (None = no limit).  A download that hangs instead of failing must
    not run into the Lambda timeout, because then no email would go out.  The abandoned thread is a daemon;
    Lambda freezes the process once the handler returns.
    """
    if seconds is None:
        return fn(arg)
    box: dict = {}

    def target():
        try:
            box["value"] = fn(arg)
        except BaseException as exc:                                # noqa: BLE001 - re-raised below, in the caller
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeoutError(f"price download still running after {seconds:.0f} s, abandoned")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _attempt_seconds(context) -> Optional[float]:
    """Time one download attempt may take: what is left minus the reserve, capped; None when there is no Lambda clock."""
    remaining = _remaining_ms(context)
    if remaining == float("inf"):
        return None
    return max(MIN_ATTEMPT_SECONDS, min(MAX_ATTEMPT_SECONDS, remaining / 1000 - RESERVE_SECONDS))


def fetch_with_retries(signal_ts: datetime, context, fetch: Callable, sleep: Callable) -> PriceFetchResult:
    """Download Universe prices, retrying on errors or thin coverage, within the Lambda's time budget."""
    last_problem = "no attempt made"
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            result = _call_with_deadline(fetch, signal_ts, _attempt_seconds(context))
            if result.coverage >= MIN_COVERAGE:
                return result
            last_problem = f"only {len(result.data)} of {len(result.requested)} stocks returned prices ({result.coverage:.0%})"
        except Exception as exc:                                    # yfinance raises many kinds of error
            last_problem = f"{type(exc).__name__}: {exc}"
        logger.warning("Price download attempt %d/%d failed: %s", attempt, FETCH_ATTEMPTS, last_problem)
        if attempt < FETCH_ATTEMPTS:
            if _remaining_ms(context) < MIN_TIME_FOR_RETRY_MS:
                last_problem += " (out of time to retry)"
                break
            sleep(FETCH_BACKOFF_SECONDS[min(attempt - 1, len(FETCH_BACKOFF_SECONDS) - 1)])
    raise StockSelectionError(f"price download failed: {last_problem}")


def _send(ses, sender: str, recipient: str, subject: str, html_body: str, text_body: str) -> None:
    ses.send_email(
        Source=sender,
        Destination={"ToAddresses": [recipient]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}, "Text": {"Data": text_body, "Charset": "UTF-8"}},
        },
    )


def process_record(bucket: str, key: str, context, s3, ses, fetch: Callable, sleep: Callable, now: Callable) -> dict:
    """Handle one successful scout result.  Never raises: failures are recorded and emailed once."""
    sender, recipient = os.environ["SES_SENDER"], os.environ["SES_RECIPIENT"]
    week = week_label(now())
    payload, signal_ts = None, None
    try:
        payload = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        signals = payload.get("signals") or []
        if not signals:
            raise StockSelectionError("scout result contains no signals")
        signal_ts = signal_time(payload)
        week = week_label(signal_ts)

        prices = fetch_with_retries(signal_ts, context, fetch, sleep)
        analysis = analyse_universe(prices.data)
        selections = select_stocks(signals, analysis, prices)
        snapshot = build_snapshot(signal_ts, signals, analysis, prices, selections)

        snapshot_key = f"snapshots/{week}/universe.csv"
        s3.put_object(Bucket=bucket, Key=snapshot_key, Body=snapshot_to_csv(snapshot).encode("utf-8"), ContentType="text/csv")
        logger.info("Snapshot saved → s3://%s/%s (%d rows)", bucket, snapshot_key, len(snapshot))

        _send(ses, sender, recipient, *build_report_email(
            week, signal_ts, payload, selections, prices, source_uri=f"s3://{bucket}/{key}", snapshot_key=snapshot_key,
            map_age_days=sector_map_age_days(date.today())))
        picks = sum(len(s.picks) for s in selections)
        logger.info("Report email sent: %d picks across %d sectors", picks, len(selections))
        return {"status": "ok", "week": week, "source_key": key, "snapshot_key": snapshot_key, "picks": picks}

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}" if not isinstance(exc, StockSelectionError) else str(exc)
        logger.error("Stock selection failed for %s: %s\n%s", key, error, traceback.format_exc())
        failed_key = None
        try:
            stamp = now().strftime("%Y%m%dT%H%M%SZ")
            failed_key = f"failed/{week}/stock_selection_{stamp}.json"
            s3.put_object(Bucket=bucket, Key=failed_key, ContentType="application/json", Body=json.dumps(
                {"source_key": key, "error": error, "traceback": traceback.format_exc()}, indent=2).encode("utf-8"))
        except Exception:
            logger.exception("Could not save the failure record")
            failed_key = None
        try:
            email = None
            if isinstance(payload, dict) and payload.get("signals") and signal_ts is not None:
                try:                                                # the sector report, in full, with a notice in place of the picks
                    email = build_report_email(week, signal_ts, payload, source_uri=f"s3://{bucket}/{key}",
                                               error=error, failed_key=failed_key)
                except Exception:
                    logger.exception("Could not render the sector report; sending the short failure email instead")
            _send(ses, sender, recipient, *(email or build_failure_email(week, key, error, failed_key)))
        except Exception:
            logger.exception("Could not send the failure email")
        return {"status": "failed", "week": week, "source_key": key, "error": error, "failed_key": failed_key}


def run(event: dict, context, s3, ses, fetch: Optional[Callable] = None,
        sleep: Callable = time.sleep, now: Callable = lambda: datetime.now(timezone.utc)) -> dict:
    """The handler with its dependencies passed in (so it can be tested without AWS or Yahoo)."""
    fetch = fetch or fetch_universe_prices
    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        if not SUCCESS_KEY.match(key):
            logger.info("Ignoring s3://%s/%s (not a successful scout result)", bucket, key)
            continue
        results.append(process_record(bucket, key, context, s3, ses, fetch, sleep, now))
    return {"processed": results}


def handler(event: dict, context) -> dict:
    """Lambda entry point."""
    import boto3                                                   # provided by the Lambda runtime

    region = os.environ.get("AWS_REGION", "eu-west-1")
    return run(event, context, boto3.client("s3"), boto3.client("ses", region_name=region))
