"""
portfolio_step.py — the Paper Portfolio's weekly step and the website's data.

Runs inside the stock-selection Lambda after the Universe Snapshot is built
(see docs/adr/0003-paper-portfolio.md), or from a stored week:

    python stock_selection_lambda/portfolio_step.py --bucket BUCKET --week 2026-W44

(the same as invoking the Lambda with {"portfolio_week": "2026-W44"}).  A stored
week is rebuilt from  snapshots/<week>/universe.csv  and the scout's result in
successful/<week>/, without fetching stock prices from Yahoo.

S3 layout
---------
portfolio/weeks/YYYY-Www.json   one record per week: the inputs that are not in the snapshot (the
                                GBP/USD rate) and the allocator's result.  Each week is built only from
                                the previous week's record, so re-running a week rewrites it.
site/portfolio.json             the website's portfolio data      } shapes in docs/site-data.md
site/ledger.json                every Trade and note               }
site/weeks/index.json           weeks with a Run Trace             }
site/weeks/YYYY-Www.json        that week's Run Trace              }

Weeks are applied in order.  Missing weeks between the last record and the week
being run are filled first: from their stored snapshot when there is one,
otherwise as a no-trade week.  Weeks after it that already have a record are
rebuilt, so the chain always reflects the latest inputs.
"""
import argparse
import io
import json
import logging
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lse_stock_analysis import portfolio as pf  # noqa: E402
from lse_stock_analysis.selection import METHODOLOGY_VERSION  # noqa: E402

logger = logging.getLogger(__name__)

WEEKS_PREFIX = "portfolio/weeks/"
SITE_PREFIX = "site/"
SITE_URL_DEFAULT = "https://arijitgupta42.github.io/robotrader/"
FX_TICKER = "GBPUSD=X"
FX_RANGE = (0.5, 3.0)                 # a GBP/USD rate outside this is treated as bad data

# Signal fields published on the website.  Left out on purpose: conviction_drivers (it quotes headlines)
# and retail_thesis (it paraphrases Reddit posts).
PUBLIC_SIGNAL_FIELDS = (
    "sector", "confidence", "convergence_type", "bear_case_probability", "disruption_type", "disruption_strength",
    "time_to_impact_weeks", "source_diversity", "rationale", "propagation", "invalidation_risk", "convergence_note",
    "key_catalysts", "correlated_sectors",
)


class PortfolioError(Exception):
    """The portfolio step cannot run for this week."""


def site_url() -> str:
    return os.environ.get("SITE_URL", SITE_URL_DEFAULT)


# ---------------------------------------------------------------------------
# Weeks
# ---------------------------------------------------------------------------

def week_after(label: str) -> str:
    year, week, _ = (pf._week_monday(label) + timedelta(days=7)).isocalendar()
    return f"{year}-W{week:02d}"


def started(week: str) -> bool:
    """Whether the Paper Portfolio exists in this week (labels sort in time order)."""
    return week >= pf.OPENING_WEEK


# ---------------------------------------------------------------------------
# Snapshot -> allocator inputs
# ---------------------------------------------------------------------------

def _num(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f                                     # NaN -> None


def _bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value) if value is not None and value == value else False


def _str(value) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def week_inputs(week: str, snapshot: Optional[pd.DataFrame], gbpusd: Optional[float],
                scout_error: Optional[str] = None) -> pf.WeekInputs:
    """
    The allocator's inputs from a Universe Snapshot.  Everything it needs (closes, Picks with their
    signal and risk numbers, the Signalled Sectors) is on the snapshot, so a live week and a replayed
    week go through exactly the same code.  No snapshot means a no-trade week.
    """
    if snapshot is None:
        return pf.WeekInputs(week=week, scout_ok=False, signals=[], picks=[], quotes={}, gbpusd=gbpusd,
                             scout_error=scout_error or "no Universe Snapshot for this week")
    quotes, picks, signals = {}, [], {}
    for row in snapshot.to_dict("records"):
        ticker = row["ticker"]
        market = _str(row.get("market")) or "LSE"
        sector = _str(row.get("primary_sector"))
        quotes[ticker] = pf.Quote(
            ticker=ticker, market=market, currency=_str(row.get("currency")) or ("USD" if market == "US" else "GBp"),
            primary_sector=sector, close=_num(row.get("close")), stale=_bool(row.get("stale")),
            price_anomaly=_bool(row.get("price_anomaly")), company=_str(row.get("company")) or ticker)
        if _bool(row.get("is_candidate")) and sector:
            signals.setdefault(sector, {"sector": sector, "confidence": _num(row.get("signal_confidence")),
                                        "convergence_type": _str(row.get("signal_convergence_type"))})
        if _bool(row.get("is_pick")):
            picks.append(pf.Pick(
                ticker=ticker, sector=sector, confidence=_num(row.get("signal_confidence")) or 0.0,
                convergence_type=_str(row.get("signal_convergence_type")) or "", setup_grade=_str(row.get("setup_grade")) or "",
                risk_score=_num(row.get("risk_score")) or 0.0, max_position_size_pct=_num(row.get("max_position_size_pct")) or 0.0,
                atr_stop_pct=_num(row.get("atr_stop_pct")) or 0.0))
    return pf.WeekInputs(week=week, scout_ok=True, signals=list(signals.values()), picks=picks, quotes=quotes, gbpusd=gbpusd)


def fx_date(snapshot: pd.DataFrame) -> date:
    """The day the week's GBP/USD rate is taken on: the US price cutoff (else the latest cutoff)."""
    cutoffs = snapshot.get("price_cutoff")
    if cutoffs is None:
        raise PortfolioError("snapshot has no price_cutoff column")
    if "market" in snapshot:
        us = snapshot.loc[snapshot["market"] == "US", "price_cutoff"].dropna()
        if len(us):
            cutoffs = us
    return date.fromisoformat(str(cutoffs.dropna().max())[:10])


def fetch_gbpusd(on: date, downloader: Optional[Callable] = None) -> Optional[float]:
    """Yahoo's GBP/USD close on `on` (or the last one before it); None if it cannot be had."""
    try:
        if downloader is None:
            from lse_stock_analysis.prices import download_batch as downloader
        frames = downloader([FX_TICKER], on - timedelta(days=7), on + timedelta(days=1))
        frame = frames.get(FX_TICKER)
        if frame is None or frame.empty:
            raise ValueError("no GBP/USD data returned")
        closes = frame["Close"].dropna()
        closes = closes[[pd.Timestamp(i).date() <= on for i in closes.index]]
        rate = float(closes.iloc[-1])
        if not FX_RANGE[0] < rate < FX_RANGE[1]:
            raise ValueError(f"implausible GBP/USD rate {rate}")
        return rate
    except Exception as exc:                                         # yfinance raises many kinds of error
        logger.warning("GBP/USD rate for %s unavailable: %s", on, exc)
        return None


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------

def _missing(exc: Exception) -> bool:
    if isinstance(exc, KeyError):
        return True
    code = getattr(exc, "response", {}).get("Error", {}).get("Code")
    return code in ("NoSuchKey", "404", "NotFound")


class Store:
    """The bucket, as the portfolio step uses it."""

    def __init__(self, s3, bucket: str):
        self.s3, self.bucket = s3, bucket

    def keys(self, prefix: str) -> list[str]:
        pages = self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix)
        return sorted(o["Key"] for page in pages for o in page.get("Contents", []))

    def get_bytes(self, key: str) -> Optional[bytes]:
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:
            if _missing(exc):
                return None
            raise

    def get_json(self, key: str):
        body = self.get_bytes(key)
        return None if body is None else json.loads(body)

    def put_json(self, key: str, value) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, ContentType="application/json",
                           Body=json.dumps(value, indent=1, ensure_ascii=False).encode("utf-8"))

    # weeks
    def record_weeks(self) -> list[str]:
        return [k[len(WEEKS_PREFIX):-5] for k in self.keys(WEEKS_PREFIX) if k.endswith(".json")]

    def record(self, week: str) -> Optional[dict]:
        return self.get_json(f"{WEEKS_PREFIX}{week}.json")

    def save_record(self, record: dict) -> None:
        self.put_json(f"{WEEKS_PREFIX}{record['week']}.json", record)

    def snapshot(self, week: str) -> Optional[pd.DataFrame]:
        body = self.get_bytes(f"snapshots/{week}/universe.csv")
        return None if body is None else pd.read_csv(io.BytesIO(body))

    def scout_result(self, week: str) -> tuple[Optional[str], Optional[dict]]:
        """The week's latest successful scout result (key, payload)."""
        keys = [k for k in self.keys(f"successful/{week}/") if k.endswith(".json")]
        if not keys:
            return None, None
        return keys[-1], self.get_json(keys[-1])


# ---------------------------------------------------------------------------
# The weekly step
# ---------------------------------------------------------------------------

def _record(week: str, result: pf.WeekResult, gbpusd: Optional[float], inputs: pf.WeekInputs,
            source_key: Optional[str], now: datetime) -> dict:
    return {
        "week": week, "portfolio_version": pf.PORTFOLIO_VERSION, "computed_at": now.isoformat(),
        "gbpusd": gbpusd, "scout_ok": inputs.scout_ok, "scout_error": inputs.scout_error,
        "snapshot_key": f"snapshots/{week}/universe.csv" if inputs.scout_ok else None, "source_key": source_key,
        "result": result.to_dict(),
    }


def _state(record: Optional[dict]) -> Optional[pf.PortfolioState]:
    return None if record is None else pf.PortfolioState.from_dict(record["result"]["state"])


def _apply_stored(store: Store, week: str, previous: Optional[pf.PortfolioState], fetch_fx: Callable,
                  now: datetime) -> dict:
    """Apply one week from what is stored for it (its snapshot, its earlier record's exchange rate)."""
    snapshot = store.snapshot(week)
    old = store.record(week)
    gbpusd = None
    if snapshot is not None:
        gbpusd = old["gbpusd"] if old and old.get("gbpusd") else fetch_fx(fx_date(snapshot))
    inputs = week_inputs(week, snapshot, gbpusd,
                         scout_error="no Universe Snapshot for this week (the scout or stock selection did not complete)")
    record = _record(week, pf.apply_week(previous, inputs), gbpusd, inputs, (old or {}).get("source_key"), now)
    store.save_record(record)
    logger.info("Portfolio week %s %s from stored data", week, "rebuilt" if old else "filled")
    return record


def run_week(store: Store, week: str, snapshot: Optional[pd.DataFrame], gbpusd: Optional[float],
             fetch_fx: Callable = fetch_gbpusd, source_key: Optional[str] = None,
             now: Callable = lambda: datetime.now(timezone.utc)) -> Optional[dict]:
    """
    Apply `week` (with its snapshot and exchange rate) to the Paper Portfolio and save its record.
    Returns the record, or None for a week before the portfolio started.
    """
    if not started(week):
        return None
    stamp = now()
    existing = store.record_weeks()
    previous = None
    if week != pf.OPENING_WEEK:
        earlier = [w for w in existing if w < week]
        if not earlier:
            raise PortfolioError(f"no portfolio record before {week}: the Paper Portfolio starts at {pf.OPENING_WEEK}, "
                                 f"run that week first")
        record = store.record(earlier[-1])
        w = week_after(earlier[-1])
        while w < week:                                              # weeks with no record: fill them in order
            record = _apply_stored(store, w, _state(record), fetch_fx, stamp)
            w = week_after(w)
        previous = _state(record)

    inputs = week_inputs(week, snapshot, gbpusd)
    record = _record(week, pf.apply_week(previous, inputs), gbpusd, inputs, source_key, stamp)
    store.save_record(record)
    logger.info("Portfolio week %s: %d trade(s), total £%.2f", week, len(record["result"]["trades"]),
                record["result"]["valuation"]["total"])

    later = [w for w in existing if w > week]
    if later:                                                       # an earlier week changed: rebuild what follows
        state, w = _state(record), week_after(week)
        while w <= later[-1]:
            state = _state(_apply_stored(store, w, state, fetch_fx, stamp))
            w = week_after(w)
    return record


def run_stored_week(store: Store, week: str, fetch_fx: Callable = fetch_gbpusd,
                    now: Callable = lambda: datetime.now(timezone.utc)) -> dict:
    """Apply a stored week (no price download) and republish the website's data, including its Run Trace."""
    if not started(week):
        raise PortfolioError(f"{week} is before the Paper Portfolio's first week, {pf.OPENING_WEEK}")
    snapshot = store.snapshot(week)
    if snapshot is None:
        raise PortfolioError(f"no Universe Snapshot for {week} in s3://{store.bucket}/snapshots/{week}/")
    source_key, payload = store.scout_result(week)
    old = store.record(week)
    gbpusd = old["gbpusd"] if old and old.get("gbpusd") else fetch_fx(fx_date(snapshot))
    record = run_week(store, week, snapshot, gbpusd, fetch_fx=fetch_fx, source_key=source_key, now=now)
    trace = build_trace(week, payload or {}, snapshot, now=now) if payload else None
    publish_site(store, week, trace, now=now)
    return {"status": "ok", "week": week, "trades": len(record["result"]["trades"]),
            "total": round(record["result"]["valuation"]["total"], 2), "gbpusd": gbpusd}


# ---------------------------------------------------------------------------
# Email summary
# ---------------------------------------------------------------------------

def email_summary(record: dict) -> dict:
    """What the weekly email shows about the Paper Portfolio."""
    result = record["result"]
    return {
        "week": record["week"], "trades": result["trades"], "valuation": result["valuation"],
        "notes": [n for n in result["notes"] if n["kind"] != "skipped"],
        "skipped": [n for n in result["notes"] if n["kind"] == "skipped"],
        "positions": len(result["state"]["positions"]), "site_url": site_url(),
    }


# ---------------------------------------------------------------------------
# Website data (shapes documented in docs/site-data.md)
# ---------------------------------------------------------------------------

def _r(value, places=4):
    return None if value is None else round(float(value), places)


def _public_position(p: dict, week: str) -> dict:
    return {
        "ticker": p["ticker"], "company": p["company"], "market": p["market"], "currency": p["currency"],
        "sector": p["primary_sector"], "quantity": _r(p["quantity"], 6), "entry_week": p["entry_week"],
        "entry_price": _r(p["entry_price"]), "entry_fx": _r(p["entry_fx"]), "cost_basis_gbp": _r(p["cost_basis_gbp"], 2),
        "stop_price": _r(p["stop_price"]), "last_price": _r(p["last_price"]), "last_fx": _r(p["last_fx"]),
        "value_gbp": _r(p["value_gbp"], 2), "pnl_gbp": _r(p["value_gbp"] - p["cost_basis_gbp"], 2),
        "weeks_held": pf.weeks_between(p["entry_week"], week),
        "weeks_to_max_hold": pf.MAX_HOLD_WEEKS - pf.weeks_between(p["clock_start_week"], week),
        "sector_absent_weeks": p["sector_absent_weeks"], "stale": p["stale"], "fx_stale": p["fx_stale"], "signal": p["signal"],
    }


def _public_trade(t: dict) -> dict:
    out = dict(t)
    for k, places in (("quantity", 6), ("price", 4), ("fx", 4), ("value_gbp", 2), ("cost_gbp", 2), ("pnl_gbp", 2)):
        out[k] = _r(out.get(k), places)
    return out


def site_portfolio(records: list[dict], now: datetime) -> dict:
    latest = records[-1]
    state, val = latest["result"]["state"], latest["result"]["valuation"]
    return {
        "updated_at": now.isoformat(), "week": latest["week"], "portfolio_version": state["portfolio_version"],
        "starting_cash": pf.STARTING_CASH, "opening_week": pf.OPENING_WEEK,
        "cash": _r(val["cash"], 2), "holdings": _r(val["holdings"], 2), "total": _r(val["total"], 2),
        "benchmark": _r(val["benchmark"], 2),
        "positions": [_public_position(p, latest["week"]) for p in state["positions"]],
        "history": [{"week": r["week"], "cash": _r(r["result"]["valuation"]["cash"], 2),
                     "holdings": _r(r["result"]["valuation"]["holdings"], 2), "total": _r(r["result"]["valuation"]["total"], 2),
                     "benchmark": _r(r["result"]["valuation"]["benchmark"], 2), "trades": len(r["result"]["trades"]),
                     "scout_ok": r["scout_ok"], "gbpusd": _r(r["gbpusd"])} for r in records],
        "rules": {
            "max_position_weight": pf.MAX_POSITION_WEIGHT, "cash_floor": pf.CASH_FLOOR, "max_positions": pf.MAX_POSITIONS,
            "min_trade_gbp": pf.MIN_TRADE_GBP, "stamp_duty": pf.STAMP_DUTY, "fx_fee": pf.FX_FEE,
            "max_hold_weeks": pf.MAX_HOLD_WEEKS, "sector_absent_weeks": pf.SECTOR_ABSENT_WEEKS,
        },
    }


def site_ledger(records: list[dict]) -> dict:
    return {
        "trades": [_public_trade(t) for r in records for t in r["result"]["trades"]],
        "notes": [{"week": r["week"], **n} for r in records for n in r["result"]["notes"]],
    }


def build_trace(week: str, payload: dict, snapshot: Optional[pd.DataFrame], selections=None, prices=None,
                email_sent: Optional[bool] = None, now: Callable = lambda: datetime.now(timezone.utc)) -> dict:
    """
    One week's Run Trace for the website: the scout's own trace block (when it sent one), its signals
    (public fields only), and what stock selection did, from the snapshot and, on a live run, the
    selections and price download.  The Paper Portfolio's actions live in site/ledger.json.
    """
    signals = [{k: s.get(k) for k in PUBLIC_SIGNAL_FIELDS if k in s} for s in payload.get("signals") or []]
    trace = {
        "week": week, "generated_at": now().isoformat(), "methodology_version": METHODOLOGY_VERSION,
        "portfolio_version": pf.PORTFOLIO_VERSION,
        "scout": {"timestamp": payload.get("timestamp"), "prompt_version": payload.get("prompt_version"),
                  "models": payload.get("models"), "macro": payload.get("macro"), "signals": signals,
                  "trace": payload.get("trace")},
        "prices": None, "analysis": None, "selection": None, "email": {"sent": email_sent},
    }
    if snapshot is not None:
        status = Counter(str(s) for s in snapshot["status"])
        ok = snapshot[snapshot["status"] == "ok"]
        trace["prices"] = {
            "requested": int(len(snapshot)), "returned": int(len(snapshot) - status.get("no_data", 0)),
            "no_data": status.get("no_data", 0), "short_history": status.get("short_history", 0),
            "stale": int(sum(_bool(v) for v in snapshot.get("stale", []))),
            "anomalies": int(sum(_bool(v) for v in snapshot.get("price_anomaly", []))),
            "unit_fixed": int(sum(1 for v in snapshot.get("unit_fixed", []) if (_num(v) or 0) > 0)),
            "cutoffs": {str(m): str(c) for m, c in snapshot.groupby("market")["price_cutoff"].max().items()}
            if "market" in snapshot else {},
        }
        trace["analysis"] = {
            "analysed": int(len(ok)), "failed": status.get("analysis_failed", 0),
            "grades": {str(k): int(v) for k, v in Counter(ok["setup_grade"].dropna()).items()},
            "setups": {str(k): int(v) for k, v in Counter(ok["swing_setup"].dropna()).items()},
            "tradeable": int(sum(_bool(v) for v in ok["tradeable"])) if "tradeable" in ok else None,
        }
        by_sector = {s.sector: s for s in selections or []}
        rows = []
        for sig in sorted({s["sector"]: s for s in signals}.values(), key=lambda s: -(s.get("confidence") or 0)):
            cands = snapshot[snapshot["primary_sector"] == sig["sector"]]
            picks = cands[[_bool(v) for v in cands["is_pick"]]]
            sel = by_sector.get(sig["sector"])
            rows.append({
                "sector": sig["sector"], "confidence": sig.get("confidence"), "convergence_type": sig.get("convergence_type"),
                "candidates": int(len(cands)),
                "eligible_checked": int(sum(cands["status"] == "ok")),
                "picks_allowed": sel.picks_allowed if sel else None,
                "picks": [{"ticker": p["ticker"], "company": p["company"], "market": p.get("market"),
                           "setup_grade": _str(p.get("setup_grade")), "risk_score": _num(p.get("risk_score")),
                           "close": _num(p.get("close"))} for p in picks.to_dict("records")],
                "runners_up": [{"ticker": r["ticker"], "company": r["company"], "reason": r.get("reason")}
                               for r in (sel.runners_up if sel else [])],
            })
        trace["selection"] = rows
    if prices is not None:
        trace["prices"] = {**(trace["prices"] or {}), "coverage": _r(prices.coverage),
                           "cutoffs": {m: str(c) for m, c in prices.cutoffs.items()}}
    return trace


def publish_site(store: Store, week: str, trace: Optional[dict], now: Callable = lambda: datetime.now(timezone.utc)) -> None:
    """Rewrite the website's portfolio and ledger from every weekly record, and add this week's Run Trace."""
    stamp = now()
    records = [r for r in (store.record(w) for w in store.record_weeks()) if r]
    if records:
        store.put_json(f"{SITE_PREFIX}portfolio.json", site_portfolio(records, stamp))
        store.put_json(f"{SITE_PREFIX}ledger.json", site_ledger(records))
    if trace is not None:
        store.put_json(f"{SITE_PREFIX}weeks/{week}.json", trace)
        index = store.get_json(f"{SITE_PREFIX}weeks/index.json") or {"weeks": []}
        index = {"updated_at": stamp.isoformat(), "weeks": sorted(set(index.get("weeks", [])) | {week})}
        store.put_json(f"{SITE_PREFIX}weeks/index.json", index)


# ---------------------------------------------------------------------------
# Quarterly report section
# ---------------------------------------------------------------------------

def quarter_summary(records: list[dict], last_weeks: int = 13) -> Optional[dict]:
    """The Paper Portfolio for the quarterly backtest report: since the start and over the last `last_weeks` weeks."""
    if not records:
        return None

    def span(rs: list[dict], start_total: float, start_bench: float) -> dict:
        end = rs[-1]["result"]["valuation"]
        trades = [t for r in rs for t in r["result"]["trades"]]
        return {
            "from_week": rs[0]["week"], "to_week": rs[-1]["week"], "weeks": len(rs),
            "value_start": _r(start_total, 2), "value_end": _r(end["total"], 2),
            "return": _r(end["total"] / start_total - 1), "benchmark_return": _r(end["benchmark"] / start_bench - 1),
            "buys": sum(t["side"] == "buy" for t in trades), "sells": sum(t["side"] == "sell" for t in trades),
            "exits_by_reason": dict(Counter(t["reason"] for t in trades if t["side"] == "sell")),
            "costs_gbp": _r(sum(t["cost_gbp"] for t in trades), 2),
            "no_trade_weeks": sum(not r["scout_ok"] for r in rs),
            "portfolio_versions": dict(Counter(r["portfolio_version"] for r in rs)),
        }

    recent = records[-last_weeks:]
    before = records[-last_weeks - 1]["result"]["valuation"] if len(records) > last_weeks else None
    return {
        "since_start": span(records, pf.STARTING_CASH, pf.STARTING_CASH),
        "recent": span(recent, before["total"] if before else pf.STARTING_CASH, before["benchmark"] if before else pf.STARTING_CASH),
        "current": records[-1]["result"]["valuation"], "positions": len(records[-1]["result"]["state"]["positions"]),
    }


def render_quarter_text(summary: Optional[dict]) -> str:
    """The quarterly report's Paper Portfolio section, as plain text."""
    if summary is None:
        return "PAPER PORTFOLIO\n  No weeks recorded yet.\n"

    def pct(x):
        return "n/a" if x is None else f"{x:+.1%}"

    lines = ["PAPER PORTFOLIO (context, not evidence: one path through a few dozen Trades, mixing selection with sizing, "
             "costs and currency)"]
    for label, s in (("Since the start", summary["since_start"]), ("Last 13 weeks", summary["recent"])):
        exits = ", ".join(f"{k} {v}" for k, v in sorted(s["exits_by_reason"].items())) or "none"
        lines.append(f"  {label} ({s['from_week']} to {s['to_week']}, {s['weeks']} weeks): £{s['value_start']:,.2f} -> "
                     f"£{s['value_end']:,.2f} ({pct(s['return'])}) vs Benchmark {pct(s['benchmark_return'])}; "
                     f"{s['buys']} buys, {s['sells']} sells (exits: {exits}); costs £{s['costs_gbp']:,.2f}; "
                     f"{s['no_trade_weeks']} no-trade week(s); versions {s['portfolio_versions']}")
    return "\n".join(lines) + "\n"


def load_records(s3, bucket: str) -> list[dict]:
    store = Store(s3, bucket)
    return [r for r in (store.record(w) for w in store.record_weeks()) if r]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Apply a stored week to the Paper Portfolio and republish the website's data.")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--week", required=True, help=f"ISO week label, e.g. {pf.OPENING_WEEK}")
    parser.add_argument("--region", default="eu-west-1")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import boto3
    print(json.dumps(run_stored_week(Store(boto3.client("s3", region_name=args.region), args.bucket), args.week), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
