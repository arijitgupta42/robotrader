"""
Email rendering for the stock-selection Lambda.

Pure functions (no AWS): they turn the selection results into an HTML and a
plain-text body, in the same visual style as the sector scout's email.
"""
import html
import json
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Optional

from lse_stock_analysis.prices import PriceFetchResult
from lse_stock_analysis.selection import SectorSelection
from lse_stock_analysis.universe import SECTOR_MAP_PATH

MAP_REVIEW_DAYS = 95            # FTSE Russell reviews are quarterly; warn when the Sector Map is older
MAX_LISTED = 12                 # tickers listed per warning before "and N more"

_CONV_COLOURS = {               # convergence type -> (foreground, background)
    "Convergent": ("#1a7a4a", "#d4edda"),
    "News-Led":   ("#1a5276", "#d6eaf8"),
    "Reddit-Led": ("#6c3483", "#e8daef"),
    "Divergent":  ("#922b21", "#fadbd8"),
}


def _latest_weekday(on_or_before: date) -> date:
    d = on_or_before
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def sector_map_age_days(today: date) -> Optional[int]:
    """Days since the Sector Map's constituents were last refreshed (None if unknown)."""
    try:
        with open(SECTOR_MAP_PATH, encoding="utf-8") as fh:
            as_of = json.load(fh)["_meta"]["constituents_as_of"]
        return (today - date.fromisoformat(as_of)).days
    except (OSError, KeyError, ValueError):
        return None


def build_warnings(prices: PriceFetchResult, map_age_days: Optional[int] = None) -> list[str]:
    """Data-quality warnings for the foot of the email, most important first."""

    def listing(items) -> str:
        items = sorted(items)
        shown = ", ".join(items[:MAX_LISTED])
        return shown + (f" and {len(items) - MAX_LISTED} more" if len(items) > MAX_LISTED else "")

    out = []
    if prices.coverage < 1.0:
        out.append(f"Prices came back for {len(prices.data)} of {len(prices.requested)} stocks "
                   f"({prices.coverage:.0%}).")
    if prices.no_data:
        out.append(f"No price data for {len(prices.no_data)} stock(s): {listing(prices.no_data)}.")
    if prices.short_history:
        out.append("Too little price history: " + listing(f"{t} ({n} bars)" for t, n in prices.short_history.items()) + ".")
    if prices.stale:
        out.append("Stale prices: " + listing(f"{t} (last bar {d})" for t, d in prices.stale.items()) + ".")
    if prices.price_anomalies:
        out.append("Price Anomalies (single-day move over 40%, so not eligible as Picks): "
                   + listing(f"{t} ({m:.0%})" for t, m in prices.price_anomalies.items()) + ".")
    if prices.unit_fixed:
        out.append("Pence/pounds switches in Yahoo's data were rescaled for: " + listing(prices.unit_fixed) + ".")
    if prices.last_bar:
        modal, count = Counter(prices.last_bar.values()).most_common(1)[0]
        if modal < _latest_weekday(prices.cutoff_date):
            out.append(f"Yahoo's latest bar for {count} stock(s) is {modal}, behind the {prices.cutoff_date} cutoff "
                       f"(data lag or a market holiday), so prices may be a day old.")
    if map_age_days is not None and map_age_days > MAP_REVIEW_DAYS:
        out.append(f"The Sector Map was last refreshed {map_age_days} days ago; a FTSE quarterly review is probably "
                   f"due (run `python -m lse_stock_analysis.universe_check`).")
    return out


def _pick_rows(sel: SectorSelection) -> list[tuple]:
    return [(p["ticker"], p["company"], p["swing_setup"], p["setup_grade"], f"{p['risk_score']}/10",
             f"{p['stop_loss_pct']:.1f}%", f"{p['max_position_pct']:.1f}%", f"{p['close']:,.2f}")
            for p in sel.picks]


_PICK_HEAD = ("Ticker", "Company", "Setup", "Grade", "Risk", "Stop-loss", "Max position", "Close")


def _table(head, rows, header_bg="#1a5276") -> str:
    th = "".join(f"<th align='left' style='padding:4px 8px;font-size:11px;color:#fff;background:{header_bg};'>{esc(h)}</th>"
                 for h in head)
    body = "".join(
        "<tr>" + "".join(f"<td style='padding:4px 8px;font-size:12px;border-bottom:1px solid #eee;'>{esc(c)}</td>" for c in r) + "</tr>"
        for r in rows)
    return f"<table cellpadding='0' cellspacing='0' width='100%' style='border-collapse:collapse;margin:6px 0;'><tr>{th}</tr>{body}</table>"


def _sector_html(sel: SectorSelection) -> str:
    fg, bg = _CONV_COLOURS.get(sel.convergence_type, ("#1a5276", "#d6eaf8"))
    badge = (f"<span style='display:inline-block;padding:2px 8px;border-radius:3px;background:{bg};color:{fg};"
             f"font-size:11px;font-weight:700;'>{esc(sel.convergence_type)}</span>")
    if not sel.investable:
        body = ("<p style='margin:6px 0;font-size:12px;color:#777;'>No stock in the Sector Map has this as its "
                "Primary Sector, so there is nothing to pick.</p>")
    elif sel.picks:
        body = _table(_PICK_HEAD, _pick_rows(sel))
    else:
        body = "<p style='margin:6px 0;font-size:12px;color:#777;'>No eligible stock this week.</p>"
    if sel.runners_up:
        body += ("<p style='margin:8px 0 2px;font-size:11px;font-weight:700;color:#777;'>CLOSE BUT NOT PICKED</p>"
                 + _table(("Ticker", "Company", "Why not"), [(r["ticker"], r["company"], r["reason"]) for r in sel.runners_up],
                          header_bg="#7f8c8d"))
    return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px;border:1px solid #e0e0e0;border-radius:6px;border-left:4px solid {fg};background:#fafafa;">
  <tr><td style="padding:14px 18px;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td><p style="margin:0;font-size:16px;font-weight:700;color:#1a5276;">{esc(sel.sector)}</p></td>
      <td align="right">{badge}</td></tr></table>
    <p style="margin:2px 0 6px;font-size:12px;color:#777;">Sector confidence {sel.confidence:.0%} &nbsp;·&nbsp;
      up to {sel.picks_allowed} pick{'s' if sel.picks_allowed != 1 else ''} &nbsp;·&nbsp; {sel.candidate_count} stock{'s' if sel.candidate_count != 1 else ''} in the sector</p>
    {body}
  </td></tr>
</table>"""


def build_picks_email(
        week:        str,
        signal_ts:   datetime,
        selections:  list[SectorSelection],
        prices:      PriceFetchResult,
        snapshot_key: Optional[str] = None,
        map_age_days: Optional[int] = None,
        ) -> tuple[str, str, str]:
    """
    Returns (subject, html_body, text_body).

    One section per Signalled Sector (strongest signal first): its Picks with
    setup, grade, risk score, stop-loss and maximum position, then the best
    runners-up with why they missed out; finally the warnings.
    """
    ordered = sorted(selections, key=lambda s: -s.confidence)
    n_picks = sum(len(s.picks) for s in ordered)
    subject = (f"LSE Stock Picks — {n_picks} pick{'s' if n_picks != 1 else ''} in "
               f"{sum(1 for s in ordered if s.picks)} of {len(ordered)} sector{'s' if len(ordered) != 1 else ''} · {week}")
    warnings = build_warnings(prices, map_age_days)

    sections_html = "\n".join(_sector_html(s) for s in ordered) or \
        "<p style='font-size:13px;color:#777;'>The sector scout reported no signals.</p>"
    warn_html = ("<div style='background:#fef9e7;border-left:4px solid #f1c40f;padding:10px 14px;border-radius:4px;margin-top:8px;'>"
                 "<p style='margin:0 0 4px;font-size:12px;font-weight:700;color:#9a7d0a;'>DATA WARNINGS</p><ul style='margin:0;padding-left:18px;'>"
                 + "".join(f"<li style='font-size:12px;color:#555;margin:2px 0;'>{esc(w)}</li>" for w in warnings)
                 + "</ul></div>") if warnings else ""
    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:720px;margin:0 auto;padding:20px;color:#222;background:#fff;">
  <div style="background:#1a5276;padding:18px 22px;border-radius:6px 6px 0 0;">
    <h1 style="margin:0;font-size:20px;color:#fff;">📊 LSE Stock Picks</h1>
    <p style="margin:4px 0 0;font-size:12px;color:#aed6f1;">{esc(week)} &nbsp;·&nbsp; prices to {prices.cutoff_date} close
      &nbsp;·&nbsp; {len(prices.data)} of {len(prices.requested)} stocks analysed{(' &nbsp;·&nbsp; ' + esc(snapshot_key)) if snapshot_key else ''}</p>
  </div>
  <div style="border:1px solid #e0e0e0;border-top:none;padding:20px 22px;border-radius:0 0 6px 6px;">
    {sections_html}
    {warn_html}
    <hr style="border:none;border-top:1px solid #eee;margin:18px 0;">
    <p style="font-size:11px;color:#aaa;margin:0;">Rule-based picks from daily-bar trend and risk analysis within the sectors the
      LSE Sector Scout flagged. Position sizes follow a 2% risk rule. Not investment advice. Always validate independently.</p>
  </div>
</body></html>"""

    lines = [f"LSE Stock Picks — {week}", f"Prices to {prices.cutoff_date} close; {len(prices.data)} of {len(prices.requested)} stocks analysed.", ""]
    for s in ordered:
        lines.append(f"{s.sector} — confidence {s.confidence:.0%}, {s.convergence_type}, up to {s.picks_allowed} pick(s)")
        if not s.investable:
            lines.append("  No stock in the Sector Map has this as its Primary Sector.")
        elif not s.picks:
            lines.append("  No eligible stock this week.")
        for p in s.picks:
            lines.append(f"  PICK {p['ticker']} ({p['company']}): {p['swing_setup']}, grade {p['setup_grade']}, risk {p['risk_score']}/10, "
                         f"stop-loss {p['stop_loss_pct']:.1f}%, max position {p['max_position_pct']:.1f}%, close {p['close']:,.2f}")
        for r in s.runners_up:
            lines.append(f"  not picked: {r['ticker']} ({r['company']}) — {r['reason']}")
        lines.append("")
    if warnings:
        lines += ["DATA WARNINGS"] + [f"  - {w}" for w in warnings] + [""]
    lines.append("Not investment advice. Always validate independently.")
    return subject, html_body, "\n".join(lines)


def build_failure_email(week: str, source_key: str, error: str, failed_key: Optional[str] = None) -> tuple[str, str, str]:
    """Short (subject, html, text) email for a week the stock selection could not complete."""
    subject = f"LSE Stock Picks FAILED — {week}"
    text = (f"The stock-selection step failed for {week}.\n\nReason: {error}\nSector scout result: {source_key}\n"
            + (f"Details saved to: {failed_key}\n" if failed_key else "")
            + "\nThe sector report itself was sent as normal; only the stock picks are missing this week.")
    body = "".join(f"<p style='font-size:13px;margin:6px 0;'>{esc(line)}</p>" for line in text.split("\n") if line)
    html_body = (f"<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body style='font-family:Arial,sans-serif;max-width:640px;"
                 f"margin:0 auto;padding:20px;'><div style='background:#922b21;padding:14px 20px;border-radius:6px 6px 0 0;'>"
                 f"<h1 style='margin:0;font-size:18px;color:#fff;'>⚠ LSE Stock Picks failed</h1></div>"
                 f"<div style='border:1px solid #e0e0e0;border-top:none;padding:16px 20px;'>{body}</div></body></html>")
    return subject, html_body, text
