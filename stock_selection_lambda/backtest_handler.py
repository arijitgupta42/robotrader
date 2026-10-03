"""
backtest_handler.py — AWS Lambda entry point for the quarterly methodology review.

Started by an EventBridge schedule on the 1st of January, April, July and October.  It reads every
Universe Snapshot, builds the backtest report (lse_stock_analysis/backtest.py), saves it to
reports/YYYY-Qn/backtest.html and backtest.json in the bucket, and emails it.  The email is the prompt to
run the review: ask Claude to review the methodology against the report (docs/methodology-review.md).
Nothing here changes the prompts or thresholds; improvements go through a reviewed pull request.

A failure is caught, recorded in reports/YYYY-Qn/failed_*.json and emailed, and the function returns
normally (the schedule has no retries, so a failure would otherwise be silent after one attempt).

Environment variables
---------------------
  BUCKET_NAME     the scout's results bucket (snapshots/ is read, reports/ is written)
  SES_SENDER      verified SES sender address
  SES_RECIPIENT   address that receives the report
  AWS_REGION      set by the Lambda runtime (SES region defaults to eu-west-1)
"""
import html
import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Callable

from backtest_report import load_snapshots
from lse_stock_analysis.backtest import build_report, render_html, render_text

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def period_label(now: datetime) -> str:
    """Quarter label, e.g. '2026-Q4'."""
    return f"{now.year}-Q{(now.month - 1) // 3 + 1}"


def _jsonable(value):
    """The report with every dict key as a string (horizons are ints), so it can be saved as JSON."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item"):                                       # numpy scalar
        return value.item()
    return value


def _send(ses, sender: str, recipient: str, subject: str, html_body: str, text_body: str) -> None:
    ses.send_email(
        Source=sender, Destination={"ToAddresses": [recipient]},
        Message={"Subject": {"Data": subject, "Charset": "UTF-8"},
                 "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}, "Text": {"Data": text_body, "Charset": "UTF-8"}}})


def run(event: dict, context, s3, ses, now: Callable = lambda: datetime.now(timezone.utc)) -> dict:
    """The handler with its dependencies passed in (so it can be tested without AWS)."""
    bucket = os.environ["BUCKET_NAME"]
    sender, recipient = os.environ["SES_SENDER"], os.environ["SES_RECIPIENT"]
    period = (event or {}).get("period") or period_label(now())
    try:
        snapshots = load_snapshots(s3, bucket)
        if len(snapshots) < 2:
            raise RuntimeError(f"only {len(snapshots)} snapshot(s) in s3://{bucket}/snapshots/, nothing to measure yet")
        report = build_report(snapshots)
        body_text, body_html = render_text(report), render_html(report)
        prefix = f"reports/{period}"
        s3.put_object(Bucket=bucket, Key=f"{prefix}/backtest.html", Body=body_html.encode("utf-8"), ContentType="text/html")
        s3.put_object(Bucket=bucket, Key=f"{prefix}/backtest.json", Body=json.dumps(_jsonable(report), indent=2).encode("utf-8"),
                      ContentType="application/json")
        intro = (f"Quarterly methodology review input for {period}. Saved to s3://{bucket}/{prefix}/. To act on it, ask Claude to run "
                 f"the methodology review (docs/methodology-review.md) against this report; it proposes changes as a pull request "
                 f"and nothing changes until you merge it.")
        _send(ses, sender, recipient, f"Methodology review: backtest report {period}",
              body_html.replace("</h2>", f"</h2><p style='font-size:12px;'>{html.escape(intro)}</p>", 1), intro + "\n\n" + body_text)
        logger.info("Backtest report %s sent (%d weeks used)", period, len(report["weeks_used"]))
        return {"status": "ok", "period": period, "weeks": len(report["weeks_used"]), "report_key": f"{prefix}/backtest.html"}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.error("Backtest report failed: %s\n%s", error, traceback.format_exc())
        failed_key = None
        try:
            failed_key = f"reports/{period}/failed_{now().strftime('%Y%m%dT%H%M%SZ')}.json"
            s3.put_object(Bucket=bucket, Key=failed_key, ContentType="application/json",
                          Body=json.dumps({"error": error, "traceback": traceback.format_exc()}, indent=2).encode("utf-8"))
        except Exception:
            logger.exception("Could not save the failure record")
            failed_key = None
        try:
            text = f"The quarterly backtest report for {period} failed.\n\nReason: {error}\n" + (f"Details: {failed_key}\n" if failed_key else "")
            _send(ses, sender, recipient, f"Methodology review FAILED: {period}",
                  "".join(f"<p style='font-family:Arial,sans-serif;font-size:13px;'>{html.escape(line)}</p>" for line in text.split("\n") if line), text)
        except Exception:
            logger.exception("Could not send the failure email")
        return {"status": "failed", "period": period, "error": error, "failed_key": failed_key}


def handler(event: dict, context) -> dict:
    """Lambda entry point."""
    import boto3                                                   # provided by the Lambda runtime
    region = os.environ.get("AWS_REGION", "eu-west-1")
    return run(event, context, boto3.client("s3"), boto3.client("ses", region_name=region))
