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
     Runs the full pipeline; on success closes the window and saves the result to S3
     (that write triggers the stock-selection Lambda, which sends the one email).
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
ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "eu-west-1"))

BUCKET_NAME      = os.environ["BUCKET_NAME"]
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

    if result["success"]:
        s3_key = _save_success(result)
        _close_window()

        # No email here: the stock-selection Lambda is triggered by this result and sends the one
        # combined report (sector signals plus stock picks), see docs/adr/0001.
        return {"statusCode": 200, "body": f"success — {len(result['signals'])} signals"}

    else:
        _save_failure(result, reason=result.get("error", "unknown_error"))
        logger.info(
            "Pipeline failed this attempt — window stays open for hourly retry. "
            "Reason: %s", result.get("error")
        )
        return {"statusCode": 200, "body": f"failed_attempt — {result.get('error')}"}
