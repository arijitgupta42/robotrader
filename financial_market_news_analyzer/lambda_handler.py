"""
lambda_handler.py — AWS Lambda entry point for LSE Sector Scout.

Execution model
---------------
This function is invoked by EventBridge Scheduler on two schedules:

  1. WEEKLY_TRIGGER  (Sunday 06:00 UTC)
     Sets the "weekly attempt window" open in SSM Parameter Store, then
     invokes itself asynchronously as ATTEMPT mode.

  2. HOURLY_RETRY    (every hour, Mon–Sun)
     Only acts if the attempt window is open (i.e. no success yet this week).
     Runs the full pipeline; on success closes the window and sends email.
     On failure leaves the window open so the next hourly tick retries.

Success = at least one SectorSignal returned AND signals list is non-empty.
Failure = any exception, 0 signals, or JSON decode failure.

Storage layout (S3)
-------------------
  bucket/
    successful/YYYY-WW/response_<ISO8601>.json   — validated signals
    failed/YYYY-WW/attempt_<ISO8601>_<reason>.json  — failed raw + reason

Environment variables (set via Terraform / console)
-----------------------------------------------------
  BUCKET_NAME          S3 bucket for results
  SES_SENDER           Verified SES email address (sender)
  SES_RECIPIENT        Email address to receive reports
  OPENROUTER_API_KEY   OpenRouter API key
  SSM_WINDOW_PARAM     SSM parameter name tracking open/closed window state
                       (default: /sector-scout/attempt-window)
  AWS_REGION           Set automatically by Lambda runtime
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import boto3

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# AWS clients (initialised at module level for Lambda container reuse)
# ---------------------------------------------------------------------------
s3  = boto3.client("s3")
ses = boto3.client("ses", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "eu-west-1"))

BUCKET_NAME      = os.environ["BUCKET_NAME"]
SES_SENDER       = os.environ["SES_SENDER"]
SES_RECIPIENT    = os.environ["SES_RECIPIENT"]
SSM_WINDOW_PARAM = os.environ.get("SSM_WINDOW_PARAM", "/sector-scout/attempt-window")


# ---------------------------------------------------------------------------
# SSM helpers — track open/closed attempt window
# ---------------------------------------------------------------------------

def _window_is_open() -> bool:
    """Return True if we are inside an active weekly attempt window."""
    try:
        resp = ssm.get_parameter(Name=SSM_WINDOW_PARAM)
        return resp["Parameter"]["Value"] == "open"
    except ssm.exceptions.ParameterNotFound:
        return False
    except Exception as exc:
        logger.warning("SSM read error (treating window as closed): %s", exc)
        return False


def _open_window() -> None:
    ssm.put_parameter(
        Name=SSM_WINDOW_PARAM,
        Value="open",
        Type="String",
        Overwrite=True,
    )
    logger.info("Attempt window OPENED in SSM.")


def _close_window() -> None:
    ssm.put_parameter(
        Name=SSM_WINDOW_PARAM,
        Value="closed",
        Type="String",
        Overwrite=True,
    )
    logger.info("Attempt window CLOSED in SSM.")


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------

def _week_prefix() -> str:
    """Returns 'YYYY-WW' string for the current ISO week."""
    now = datetime.now(timezone.utc)
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _iso_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _save_success(payload: dict) -> str:
    week  = _week_prefix()
    key   = f"successful/{week}/response_{_iso_ts()}.json"
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=key,
        Body=json.dumps(payload, indent=2, default=str),
        ContentType="application/json",
    )
    logger.info("SUCCESS stored → s3://%s/%s", BUCKET_NAME, key)
    return key


def _save_failure(payload: dict, reason: str) -> str:
    week  = _week_prefix()
    safe  = reason.replace(" ", "_")[:40]
    key   = f"failed/{week}/attempt_{_iso_ts()}_{safe}.json"
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=key,
        Body=json.dumps(payload, indent=2, default=str),
        ContentType="application/json",
    )
    logger.info("FAILURE stored → s3://%s/%s", BUCKET_NAME, key)
    return key


# ---------------------------------------------------------------------------
# Email formatter
# ---------------------------------------------------------------------------

# Convergence type → badge colours (inline CSS for email client compatibility)
_CONV_BADGE: dict = {
    "Convergent":  ("✦ CONVERGENT",  "#1a7a4a", "#d4edda"),   # green
    "News-Led":    ("◈ NEWS-LED",    "#1a5276", "#d6eaf8"),   # blue
    "Reddit-Led":  ("◉ REDDIT-LED",  "#6c3483", "#e8daef"),   # purple
    "Divergent":   ("⚡ DIVERGENT",  "#922b21", "#fadbd8"),   # red
}

# Source diversity → descriptive label + colour
def _diversity_label(score: float) -> tuple[str, str]:
    if score >= 0.7:
        return "High source diversity", "#1a7a4a"
    if score >= 0.4:
        return "Moderate source diversity", "#b7770d"
    return "Low source diversity", "#922b21"


def _build_email(signals: list, macro: str, cycle_ts: str, s3_key: str) -> tuple[str, str]:
    """Returns (subject, html_body) for SES."""
    subject = (
        f"LSE Sector Scout — {len(signals)} signal{'s' if len(signals) != 1 else ''} "
        f"· {datetime.now(timezone.utc).strftime('%d %b %Y')}"
    )

    rows = []
    for sig in signals:
        bull_pct  = int(sig["confidence"] * 100)
        bear_pct  = int(sig["bear_case_probability"] * 100)
        drivers   = "".join(
            f"<li style='margin:2px 0;font-size:12px;color:#555;'>{d}</li>"
            for d in sig.get("conviction_drivers", [])
        )
        catalysts  = " &nbsp;|&nbsp; ".join(sig.get("key_catalysts", []))
        correlated = ", ".join(sig.get("correlated_sectors", []))

        # Convergence badge
        conv_type  = sig.get("convergence_type", "News-Led")
        conv_label, conv_fg, conv_bg = _CONV_BADGE.get(
            conv_type, ("◈ NEWS-LED", "#1a5276", "#d6eaf8")
        )
        conv_note  = sig.get("convergence_note", "")
        conv_badge_html = (
            f"<span style='display:inline-block;padding:2px 8px;border-radius:3px;"
            f"background:{conv_bg};color:{conv_fg};font-size:11px;font-weight:700;"
            f"letter-spacing:0.3px;'>{conv_label}</span>"
        )

        # Retail thesis row — only shown when it has real content
        retail_thesis = sig.get("retail_thesis", "")
        retail_html   = ""
        if retail_thesis and retail_thesis not in ("No Reddit signal", "No Reddit signal (consolidator fallback)"):
            retail_html = (
                f"<p style='margin:0 0 3px;font-size:12px;'>"
                f"<b>Reddit crowd:</b> {retail_thesis}</p>"
            )

        # Convergence note row
        conv_note_html = ""
        if conv_note:
            conv_note_html = (
                f"<p style='margin:0 0 3px;font-size:12px;color:#555;'>"
                f"<b>Convergence:</b> {conv_note}</p>"
            )

        # Source diversity indicator
        diversity     = sig.get("source_diversity", 0.0)
        div_text, div_colour = _diversity_label(diversity)
        diversity_html = (
            f"<span style='font-size:11px;color:{div_colour};'>"
            f"● {div_text} ({diversity:.0%})</span>"
        )

        # Border colour follows convergence type
        border_colour = conv_fg

        rows.append(f"""
<table width="100%" cellpadding="0" cellspacing="0"
       style="margin-bottom:20px;border:1px solid #e0e0e0;border-radius:6px;
              border-left:4px solid {border_colour};background:#fafafa;">
  <tr>
    <td style="padding:14px 18px;">
      <!-- Header row: sector name + convergence badge -->
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:6px;">
        <tr>
          <td>
            <p style="margin:0;font-size:16px;font-weight:700;color:#1a5276;">
              {sig['sector']}
            </p>
          </td>
          <td align="right">{conv_badge_html}</td>
        </tr>
      </table>
      <!-- Subtitle: disruption + diversity -->
      <p style="margin:0 0 8px;font-size:12px;color:#777;">
        {sig['disruption_type']} &nbsp;·&nbsp; ~{sig['time_to_impact_weeks']} week(s) to impact
        &nbsp;·&nbsp; Disruption strength: {int(sig['disruption_strength']*100)}%
        &nbsp;·&nbsp; {diversity_html}
      </p>
      <!-- Bull/Bear bars -->
      <table cellpadding="0" cellspacing="0" style="margin-bottom:10px;">
        <tr>
          <td style="width:55px;font-size:11px;color:#27ae60;font-weight:700;">BULL {bull_pct}%</td>
          <td><div style="width:{bull_pct*2}px;height:10px;background:#27ae60;border-radius:3px;"></div></td>
        </tr>
        <tr><td colspan="2" style="height:3px;"></td></tr>
        <tr>
          <td style="width:55px;font-size:11px;color:#e74c3c;font-weight:700;">BEAR {bear_pct}%</td>
          <td><div style="width:{bear_pct*2}px;height:10px;background:#e74c3c;border-radius:3px;"></div></td>
        </tr>
      </table>
      <p style="margin:0 0 3px;font-size:12px;"><b>Rationale:</b> {sig.get('rationale','')}</p>
      <p style="margin:0 0 3px;font-size:12px;"><b>Mechanism:</b> {sig.get('propagation','')}</p>
      <p style="margin:0 0 3px;font-size:12px;"><b>Kill switch:</b> {sig.get('invalidation_risk','')}</p>
      {conv_note_html}
      {retail_html}
      {"<p style='margin:0 0 3px;font-size:12px;'><b>Catalysts:</b> " + catalysts + "</p>" if catalysts else ""}
      {"<p style='margin:0 0 3px;font-size:12px;'><b>Also watch:</b> " + correlated + "</p>" if correlated else ""}
      {"<p style='margin:6px 0 2px;font-size:12px;'><b>Evidence:</b></p><ul style='margin:2px 0;padding-left:18px;'>" + drivers + "</ul>" if drivers else ""}
    </td>
  </tr>
</table>""")

    signals_html = "\n".join(rows)
    macro_html   = f"""
<div style="background:#eaf2f8;border-left:4px solid #2980b9;padding:10px 14px;
            border-radius:4px;margin-bottom:22px;">
  <p style="margin:0;font-size:12px;font-weight:700;color:#2980b9;">MACRO REGIME</p>
  <p style="margin:4px 0 0;font-size:13px;color:#333;">{macro}</p>
</div>""" if macro else ""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:680px;
             margin:0 auto;padding:20px;color:#222;background:#fff;">
  <div style="background:#1a5276;padding:18px 22px;border-radius:6px 6px 0 0;">
    <h1 style="margin:0;font-size:20px;color:#fff;letter-spacing:0.5px;">
      📈 LSE Sector Scout
    </h1>
    <p style="margin:4px 0 0;font-size:12px;color:#aed6f1;">
      {cycle_ts} &nbsp;·&nbsp; {len(signals)} swing signal{'s' if len(signals) != 1 else ''} detected
      &nbsp;·&nbsp; Full data: s3://{BUCKET_NAME}/{s3_key}
    </p>
  </div>
  <div style="border:1px solid #e0e0e0;border-top:none;padding:20px 22px;
              border-radius:0 0 6px 6px;">
    {macro_html}
    {signals_html}
    <hr style="border:none;border-top:1px solid #eee;margin:18px 0;">
    <p style="font-size:11px;color:#aaa;margin:0;">
      Generated by LSE Sector Scout · OpenRouter LLM pipeline ·
      Not investment advice. Always validate signals independently.
    </p>
  </div>
</body></html>"""

    return subject, html


# ---------------------------------------------------------------------------
# Signal outcome tracking
# ---------------------------------------------------------------------------

def _save_outcome_stubs(signals: list, cycle_ts: str, s3_key: str) -> None:
    """
    Write one outcome-stub record per signal to S3 immediately after a
    successful cycle.  Each stub captures the signal as emitted and
    reserves slots for realised price data to be filled in later
    (manually or by a separate outcome-checker Lambda).

    Layout:
      outcomes/YYYY-WW/stubs_<ISO8601>.json

    Stub schema:
      {
        "signal_ts":          ISO8601 string — when the signal was emitted
        "sector":             str
        "confidence":         float
        "disruption_type":    str
        "convergence_type":   str
        "time_to_impact_weeks": int
        "source_s3_key":      str — the successful run that produced it
        "outcome_2w":         null  ← to be filled: % price change at 2 weeks
        "outcome_4w":         null  ← to be filled: % price change at 4 weeks
        "outcome_6w":         null  ← to be filled: % price change at 6 weeks
        "hit":                null  ← to be filled: bool, true if positive return
        "notes":              ""    ← free-text for manual annotation
      }

    These stubs are intentionally simple so they can be read and updated
    by a lightweight outcome-checker without a database dependency.
    """
    week    = _week_prefix()
    ts      = _iso_ts()
    stubs   = [
        {
            "signal_ts":            cycle_ts,
            "sector":               s.get("sector", ""),
            "confidence":           s.get("confidence", 0.0),
            "disruption_type":      s.get("disruption_type", ""),
            "convergence_type":     s.get("convergence_type", ""),
            "source_diversity":     s.get("source_diversity", 0.0),
            "time_to_impact_weeks": s.get("time_to_impact_weeks", 0),
            "source_s3_key":        s3_key,
            "outcome_2w":           None,
            "outcome_4w":           None,
            "outcome_6w":           None,
            "hit":                  None,
            "notes":                "",
        }
        for s in signals
    ]
    key = f"outcomes/{week}/stubs_{ts}.json"
    try:
        s3.put_object(
            Bucket      = BUCKET_NAME,
            Key         = key,
            Body        = json.dumps(stubs, indent=2, default=str),
            ContentType = "application/json",
        )
        logger.info("Outcome stubs saved → s3://%s/%s (%d stubs)", BUCKET_NAME, key, len(stubs))
    except Exception as exc:
        # Non-fatal — outcome tracking failure must not block the email send
        logger.error("Could not save outcome stubs (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Core pipeline runner
# ---------------------------------------------------------------------------

def _run_pipeline() -> dict:
    """
    Run the full scrape → LLM analysis pipeline.
    Returns a result dict with keys: success, signals, macro, error, raw_output.
    """
    # Import here so Lambda only loads heavy deps when actually running
    from sector_scout import SectorScout  # noqa: PLC0415

    result: Dict[str, Any] = {
        "timestamp": _iso_ts(),
        "success":   False,
        "signals":   [],
        "macro":     "",
        "error":     None,
        "raw_output": None,
    }

    try:
        scout   = SectorScout()
        signals, llm_succeeded = scout.run_cycle()

        if not llm_succeeded:
            result["error"] = (
                "llm_not_reached — all OpenRouter models failed; signals (if any) are "
                "keyword-heuristic only and not suitable for trading decisions"
            )
            logger.warning(result["error"])
            return result

        if not signals:
            result["error"] = "zero_signals — LLM returned empty signals list (treated as failure)"
            logger.warning(result["error"])
            return result

        result["success"]  = True
        result["signals"]  = [s.to_dict() for s in signals]
        result["macro"]    = signals[0].macro_regime_summary if signals else ""
        logger.info("Pipeline succeeded: %d signals", len(signals))

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["raw_output"] = traceback.format_exc()
        logger.error("Pipeline exception: %s", result["error"])

    return result


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def handler(event: dict, context) -> dict:
    """
    Main Lambda handler.

    event.source == "aws.scheduler" + event.detail-type == "WeeklyTrigger"
        → Open the attempt window, then immediately attempt the pipeline.
          (EventBridge fires this once weekly; Lambda itself retries hourly
           via a separate EventBridge rule if the window stays open.)

    event.source == "aws.scheduler" + event.detail-type == "HourlyRetry"
        → Only run if the window is open (no success yet this week).

    Any other invocation → run the pipeline unconditionally (manual test).
    """
    detail_type = event.get("detail-type", "")
    logger.info("Handler invoked — detail-type=%r", detail_type)

    # ---- Weekly trigger: open window + attempt immediately ----
    if detail_type == "WeeklyTrigger":
        _open_window()
        logger.info("WeeklyTrigger: window opened, running pipeline now.")

    # ---- Hourly retry: skip if window already closed ----
    elif detail_type == "HourlyRetry":
        if not _window_is_open():
            logger.info("HourlyRetry: window is closed (already succeeded this week). Skipping.")
            return {"statusCode": 200, "body": "window_closed"}
        logger.info("HourlyRetry: window is open, running pipeline.")

    # ---- Manual / test invocation ----
    else:
        logger.info("Manual invocation — running pipeline unconditionally.")

    # ---- Run the pipeline ----
    result = _run_pipeline()
    cycle_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if result["success"]:
        s3_key = _save_success(result)
        _close_window()

        # Save outcome stubs for later hit-rate tracking
        _save_outcome_stubs(result["signals"], cycle_ts, s3_key)

        # Send email
        subject, html_body = _build_email(
            signals  = result["signals"],
            macro    = result["macro"],
            cycle_ts = cycle_ts,
            s3_key   = s3_key,
        )
        try:
            ses.send_email(
                Source=SES_SENDER,
                Destination={"ToAddresses": [SES_RECIPIENT]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body":    {"Html": {"Data": html_body, "Charset": "UTF-8"}},
                },
            )
            logger.info("Email sent to %s", SES_RECIPIENT)
        except Exception as exc:
            logger.error("SES send failed (signals saved to S3 anyway): %s", exc)

        return {"statusCode": 200, "body": f"success — {len(result['signals'])} signals"}

    else:
        _save_failure(result, reason=result.get("error", "unknown_error"))
        logger.info(
            "Pipeline failed this attempt — window stays open for hourly retry. "
            "Reason: %s", result.get("error")
        )
        return {"statusCode": 200, "body": f"failed_attempt — {result.get('error')}"}
