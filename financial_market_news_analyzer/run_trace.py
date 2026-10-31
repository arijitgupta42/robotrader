"""
run_trace.py — the Run Trace: what each stage of one scout run did, for the project website.

Counts, names, models and timings only.  Never put headline, summary or Reddit post
text in here: the trace is published publicly (see docs/site-data.md).

The pipeline modules call the recording functions as they go; the Lambda handler
calls reset() before a run and adds to_dict() to the saved result as "trace".
Recording is thread-safe (the Reddit pass runs in a background thread) and never
raises, so tracing can never break a run.
"""
import copy
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_trace: dict = {}
_started: float = 0.0


def reset(trigger: str) -> None:
    """Start a new trace; `trigger` is how the run started (WeeklyTrigger, HourlyRetry or manual)."""
    global _trace, _started
    with _lock:
        _started = time.monotonic()
        _trace = {
            "trigger": trigger,
            "feeds": [],            # one entry per news source
            "headlines": None,      # the sieve: duplicates, stale, cap
            "reddit": None,
            "llm": {},              # pass name -> models tried, model that answered, fallback, seconds
            "signals": {},          # counts through the passes
            "timings": {},          # seconds per stage
        }


def _safe(fn):
    def wrapper(*args, **kwargs):
        try:
            with _lock:
                fn(*args, **kwargs)
        except Exception as exc:                                     # tracing must never break the run
            logger.debug("run_trace: %s failed: %s", fn.__name__, exc)
    return wrapper


@_safe
def feed(name: str, fetched: int, ok: bool, error: Optional[str] = None) -> None:
    """A news source was fetched: `fetched` headlines came back (before deduplication and the age filter)."""
    _trace.setdefault("feeds", []).append({"name": name, "fetched": fetched, "ok": ok, "error": error,
                                           "fresh": None, "kept": None})


@_safe
def feed_counts(fresh: dict, kept: dict) -> None:
    """Per source: headlines left after deduplication and the age filter, and after the per-source cap."""
    for entry in _trace.get("feeds", []):
        entry["fresh"] = fresh.get(entry["name"], 0)
        entry["kept"] = kept.get(entry["name"], 0)


@_safe
def headlines(**counts) -> None:
    _trace["headlines"] = {**(_trace.get("headlines") or {}), **counts}


@_safe
def reddit(**counts) -> None:
    _trace["reddit"] = {**(_trace.get("reddit") or {}), **counts}


@_safe
def reddit_request(label: str, status: str, posts: int = 0, retried: bool = False) -> None:
    """
    One Reddit request: status is 'ok', 'empty', 'throttled' (HTTP 429 and no successful retry) or 'failed';
    `retried` means a first HTTP 429 was retried.
    """
    r = _trace.get("reddit") or {}
    r.setdefault("requests", []).append({"label": label, "status": status, "posts": posts, "retried": retried})
    _trace["reddit"] = r


@_safe
def llm_pass(name: str, models: list, model_used: Optional[str], ok: bool, seconds: float) -> None:
    """One LLM pass (news, reddit or consolidation)."""
    primary = models[0] if models else None
    _trace.setdefault("llm", {})[name] = {
        "models": list(models), "model_used": model_used, "ok": ok, "seconds": round(seconds, 1),
        "fallback": bool(model_used and primary and not model_used.startswith(primary)),
    }


@_safe
def signals(**counts) -> None:
    _trace["signals"] = {**_trace.get("signals", {}), **counts}


@_safe
def timing(stage: str, seconds: float) -> None:
    _trace.setdefault("timings", {})[stage] = round(seconds, 1)


def to_dict() -> dict:
    """A copy of the trace, with the total run time so far."""
    with _lock:
        out = copy.deepcopy(_trace)
        if out:
            out.setdefault("timings", {})["total"] = round(time.monotonic() - _started, 1)
        return out
