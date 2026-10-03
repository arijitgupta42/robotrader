"""
Backtest report: how did the Picks, the Candidates and the whole Universe do afterwards?

Input is the stored weekly Universe Snapshots (one DataFrame per ISO week, as built by
selection.build_snapshot).  A Forward Return is a stock's close in a later snapshot over its close in
the snapshot, 2, 4 and 6 weeks on.  A **hit** is a stock whose Forward Return beats the equal-weight
average Forward Return of the whole Universe over the same window, so a rising or falling market does
not by itself make a week look good or bad.

The report compares Picks vs Candidates vs the Universe, and breaks Candidates down by Sector Signal
confidence band, convergence type, Setup Grade, Swing Setup, methodology version and prompt version, so
a change to the rules or prompts can be judged before and after.

Every figure carries its sample size, and anything under MIN_OBS stock-weeks or MIN_WEEKS weeks is marked
"indicative": consecutive weeks overlap (a 6-week return shares five weeks with the next week's), stocks in
one sector move together, and there are only a few Picks a week, so the report is evidence to weigh, not
proof.  Known biases: the Universe is today's FTSE 350 (survivorship), and snapshots taken at different
times carry closes adjusted for dividends as known at that time, which can shift a return by roughly a
dividend yield when a live snapshot is compared with a backfilled one.

Pure functions: no AWS, no network.
"""
import html
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

HORIZONS = (2, 4, 6)
MIN_OBS = 30                  # stock-weeks below which a figure is "indicative"
MIN_WEEKS = 8                 # distinct weeks below which a figure is "indicative"
UNKNOWN_CONVERGENCE = "Unknown"   # backfilled weeks from before the scout recorded convergence_type

GROUPS = ("Picks", "Candidates", "Universe")
BREAKDOWNS = ("confidence_band", "signal_convergence_type", "setup_grade", "swing_setup",
              "methodology_version", "prompt_version")


def week_offset(label: str, weeks: int) -> str:
    """ISO week label `weeks` after `label` ('2026-W40', 2 -> '2026-W42')."""
    year, week = label.split("-W")
    d = date.fromisocalendar(int(year), int(week), 1) + timedelta(weeks=weeks)
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def confidence_band(confidence) -> Optional[str]:
    """The bands that decide how many Picks a signal earns (see selection.picks_allowed)."""
    if confidence is None or pd.isna(confidence):
        return None
    if confidence > 0.80:
        return "high (>0.80)"
    if confidence >= 0.65:
        return "mid (0.65-0.80)"
    return "low (<0.65)"


def excluded_weeks(snapshots: dict[str, pd.DataFrame]) -> dict[str, str]:
    """Weeks to leave out, with the reason: those whose signals carry no convergence type (keyword-fallback results)."""
    out = {}
    for week, snap in snapshots.items():
        cands = snap[snap["is_candidate"].astype(bool)]
        if len(cands) and (cands["signal_convergence_type"] == UNKNOWN_CONVERGENCE).all():
            out[week] = "signals came from the scout's keyword fallback, not the LLM"
    return out


def forward_returns(snapshots: dict[str, pd.DataFrame], horizons=HORIZONS) -> pd.DataFrame:
    """
    One row per stock, week and horizon that has a later snapshot: `ret`, the Universe's equal-weight
    mean `universe_mean` for that week and horizon, `excess` = ret - universe_mean, `hit` = excess > 0,
    plus the snapshot's flags (is_candidate, is_pick, signal confidence, convergence, grade, setup, versions).
    """
    keep = ["is_candidate", "is_pick", "signal_confidence", "signal_convergence_type", "setup_grade", "swing_setup",
            "primary_sector", "methodology_version", "prompt_version"]
    frames = []
    for week, snap in snapshots.items():
        base = snap.drop_duplicates("ticker").set_index("ticker")
        for h in horizons:
            later = snapshots.get(week_offset(week, h))
            if later is None:
                continue
            c1 = later.drop_duplicates("ticker").set_index("ticker")["close"]
            joined = pd.concat([base["close"], c1], axis=1, keys=["c0", "c1"]).dropna()
            joined = joined[(joined["c0"] > 0) & (joined["c1"] > 0)]
            if joined.empty:
                continue
            ret = joined["c1"] / joined["c0"] - 1
            frame = pd.DataFrame({"week": week, "horizon": h, "ticker": ret.index, "ret": ret.to_numpy()})
            frame["universe_mean"] = float(ret.mean())
            frame["excess"] = frame["ret"] - frame["universe_mean"]
            frame["hit"] = frame["excess"] > 0
            flags = base.reindex(frame["ticker"])
            for col in keep:
                frame[col] = flags[col].to_numpy() if col in flags else None
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["week", "horizon", "ticker", "ret", "universe_mean", "excess", "hit"] + keep)
    out = pd.concat(frames, ignore_index=True)
    out["is_candidate"] = out["is_candidate"].astype(bool)
    out["is_pick"] = out["is_pick"].astype(bool)
    out["confidence_band"] = out["signal_confidence"].map(confidence_band)
    return out


def summarise(frame: pd.DataFrame) -> dict:
    """Sample size and performance of one set of stock-weeks."""
    n = len(frame)
    weeks = frame["week"].nunique() if n else 0
    weekly = frame.groupby("week")["excess"].mean() if n else pd.Series(dtype=float)
    se = float(weekly.std(ddof=1) / np.sqrt(len(weekly))) if len(weekly) > 1 else float("nan")
    return {
        "n": n, "weeks": weeks,
        "mean_ret": float(frame["ret"].mean()) if n else float("nan"),
        "median_ret": float(frame["ret"].median()) if n else float("nan"),
        "mean_excess": float(frame["excess"].mean()) if n else float("nan"),
        "hit_rate": float(frame["hit"].mean()) if n else float("nan"),
        "excess_se": se,
        "indicative": n < MIN_OBS or weeks < MIN_WEEKS,
    }


def _group(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    return {"Picks": frame[frame["is_pick"]], "Candidates": frame[frame["is_candidate"]], "Universe": frame}[name]


def build_report(snapshots: dict[str, pd.DataFrame], horizons=HORIZONS, exclude: Optional[dict[str, str]] = None) -> dict:
    """
    The whole report as plain data:
      weeks_used, weeks_excluded {week: reason}, horizons,
      headline    {group: {horizon: summary}}   Picks vs Candidates vs Universe,
      breakdowns  {dimension: {bucket: {horizon: summary}}}   for the Candidates,
      pick_count  Picks per week (the rules often pick nothing),
      notes       things to keep in mind when reading it.
    """
    exclude = excluded_weeks(snapshots) if exclude is None else exclude
    used = {w: s for w, s in snapshots.items() if w not in exclude}
    fr = forward_returns(used, horizons)
    headline = {g: {h: summarise(_group(fr[fr["horizon"] == h], g)) for h in horizons} for g in GROUPS}

    cands = fr[fr["is_candidate"]]
    breakdowns: dict = {}
    for dim in BREAKDOWNS:
        if dim not in cands or cands[dim].isna().all():
            continue
        buckets = {}
        for value, part in cands.dropna(subset=[dim]).groupby(dim):
            buckets[str(value)] = {h: summarise(part[part["horizon"] == h]) for h in horizons}
        if buckets:
            breakdowns[dim] = buckets

    picks_per_week = {w: int(s["is_pick"].astype(bool).sum()) for w, s in sorted(used.items())}
    notes = [
        "A hit means beating the equal-weight average of the whole Universe over the same window.",
        "Consecutive weeks overlap and stocks in a sector move together, so treat figures marked "
        f"'indicative' (under {MIN_OBS} stock-weeks or {MIN_WEEKS} weeks) as hints only.",
        "Survivorship bias: the Universe is today's FTSE 350. Dividend adjustment: closes in snapshots taken at different "
        "times are adjusted as known then, so a return across a live and a backfilled snapshot can be off by about a dividend yield.",
        f"{sum(1 for n in picks_per_week.values() if n == 0)} of {len(picks_per_week)} weeks had no Picks "
        f"({sum(picks_per_week.values())} Picks in total), so Pick-based figures rest on very few observations; "
        "the Candidate breakdowns have far more.",
    ]
    return {"weeks_used": sorted(used), "weeks_excluded": dict(exclude), "horizons": list(horizons),
            "headline": headline, "breakdowns": breakdowns, "pick_count": picks_per_week, "notes": notes}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _pct(x, signed=False) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def _row(label: str, s: dict) -> list[str]:
    flag = " (indicative)" if s["indicative"] else ""
    se = f" ±{s['excess_se'] * 100:.1f}" if not pd.isna(s["excess_se"]) else ""
    return [label, str(s["n"]), str(s["weeks"]), _pct(s["mean_ret"], True), _pct(s["median_ret"], True),
            _pct(s["mean_excess"], True) + se, _pct(s["hit_rate"]) + flag]


_HEAD = ["", "stock-weeks", "weeks", "mean return", "median", "mean excess (±1 se)", "hit rate"]


def _tables(report: dict):
    """(title, rows) blocks shared by the text and HTML renderings."""
    blocks = []
    for h in report["horizons"]:
        blocks.append((f"{h}-week Forward Return: Picks vs Candidates vs Universe",
                       [_row(g, report["headline"][g][h]) for g in GROUPS]))
    for dim, buckets in report["breakdowns"].items():
        for h in report["horizons"]:
            rows = [_row(b, hs[h]) for b, hs in sorted(buckets.items()) if hs[h]["n"]]
            if rows:
                blocks.append((f"Candidates by {dim.replace('_', ' ')}, {h} weeks", rows))
    return blocks


def render_text(report: dict) -> str:
    lines = [f"Backtest report: {len(report['weeks_used'])} weeks ({report['weeks_used'][0] if report['weeks_used'] else '-'} to "
             f"{report['weeks_used'][-1] if report['weeks_used'] else '-'})"]
    for w, why in report["weeks_excluded"].items():
        lines.append(f"Excluded {w}: {why}")
    for title, rows in _tables(report):
        lines += ["", title]
        table = [_HEAD] + rows
        widths = [max(len(r[i]) for r in table) for i in range(len(_HEAD))]
        lines += ["  " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(r)) for r in table]
    lines += ["", "Picks per week: " + ", ".join(f"{w[-3:]}:{n}" for w, n in report["pick_count"].items()), "", "Notes"]
    lines += [f"  - {n}" for n in report["notes"]]
    return "\n".join(lines)


def render_html(report: dict) -> str:
    e = html.escape
    parts = [f"<h2 style='margin:0 0 6px;'>Backtest report</h2><p style='font-size:12px;color:#555;'>{len(report['weeks_used'])} weeks "
             f"({e(report['weeks_used'][0]) if report['weeks_used'] else '-'} to {e(report['weeks_used'][-1]) if report['weeks_used'] else '-'})"
             + "".join(f"<br>Excluded {e(w)}: {e(why)}" for w, why in report["weeks_excluded"].items()) + "</p>"]
    for title, rows in _tables(report):
        th = "".join(f"<th align='left' style='padding:3px 8px;font-size:11px;color:#fff;background:#1a5276;'>{e(c)}</th>" for c in _HEAD)
        body = "".join("<tr>" + "".join(f"<td style='padding:3px 8px;font-size:12px;border-bottom:1px solid #eee;'>{e(c)}</td>" for c in r) + "</tr>"
                       for r in rows)
        parts.append(f"<h3 style='margin:16px 0 4px;font-size:13px;color:#1a5276;'>{e(title)}</h3>"
                     f"<table cellpadding='0' cellspacing='0' style='border-collapse:collapse;'><tr>{th}</tr>{body}</table>")
    parts.append("<p style='font-size:12px;color:#555;margin-top:14px;'><b>Picks per week:</b> "
                 + ", ".join(f"{e(w[-3:])}: {n}" for w, n in report["pick_count"].items()) + "</p>")
    parts.append("<ul style='font-size:12px;color:#555;'>" + "".join(f"<li>{e(n)}</li>" for n in report["notes"]) + "</ul>")
    return ("<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body style='font-family:Arial,Helvetica,sans-serif;"
            "max-width:900px;margin:0 auto;padding:20px;color:#222;'>" + "".join(parts) + "</body></html>")
