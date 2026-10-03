"""
Email rendering for the stock-selection Lambda.

Pure functions (no AWS).  The week's single email is rendered here: the sector
scout's Sector Signals (macro regime plus one card per signal, in the scout's
original layout) with each Signalled Sector's Picks inside its card.  If the
picks cannot be computed, the same email is sent with a failure notice in place
of the picks, so the sector report is never lost.
"""
import html
import json
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Optional

from lse_stock_analysis.prices import PriceFetchResult
from lse_stock_analysis.selection import SectorSelection
from lse_stock_analysis.universe import MARKETS, SECTOR_MAP_PATH, market_of

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
    for market in MARKETS:
        bars = {t: d for t, d in prices.last_bar.items() if market_of(t) == market}
        if not bars:
            continue
        modal, count = Counter(bars.values()).most_common(1)[0]
        cutoff = prices.cutoffs.get(market, prices.cutoff_date)
        if modal < _latest_weekday(cutoff):
            out.append(f"Yahoo's latest bar for {count} {market} stock(s) is {modal}, behind the {cutoff} cutoff "
                       f"(data lag or a market holiday), so prices may be a day old.")
    if map_age_days is not None and map_age_days > MAP_REVIEW_DAYS:
        out.append(f"The Sector Map was last refreshed {map_age_days} days ago; a FTSE quarterly review is probably "
                   f"due (run `python -m lse_stock_analysis.universe_check`).")
    return out


# ---------------------------------------------------------------------------
# Picks (inside each sector's card)
# ---------------------------------------------------------------------------

_PICK_HEAD = ("Ticker", "Mkt", "Company", "Setup", "Grade", "Risk", "Stop-loss", "Max position", "Close")
_BADGE_LABEL = {"Convergent": "✦ CONVERGENT", "News-Led": "◈ NEWS-LED", "Reddit-Led": "◉ REDDIT-LED", "Divergent": "⚡ DIVERGENT"}
_NO_REDDIT = ("No Reddit signal", "No Reddit signal (consolidator fallback)")


def price_text(close: float, ticker: str) -> str:
    """A close in its own currency: pence for LSE stocks, dollars for US stocks."""
    return f"{close:,.2f}p" if market_of(ticker) == "LSE" else f"${close:,.2f}"


def cutoff_text(prices: PriceFetchResult) -> str:
    """'prices to 2026-10-02 close', with the US date added when the two markets' cutoffs differ."""
    us = prices.cutoffs.get("US")
    extra = f" (US {us})" if us and us != prices.cutoff_date else ""
    return f"prices to {prices.cutoff_date} close{extra}"


def _pick_rows(sel: SectorSelection) -> list[tuple]:
    return [(p["ticker"], market_of(p["ticker"]), p["company"], p["swing_setup"], p["setup_grade"], f"{p['risk_score']}/10",
             f"{p['stop_loss_pct']:.1f}%", f"{p['max_position_pct']:.1f}%", price_text(p["close"], p["ticker"]))
            for p in sel.picks]


def _table(head, rows, header_bg="#1a5276") -> str:
    th = "".join(f"<th align='left' style='padding:4px 8px;font-size:11px;color:#fff;background:{header_bg};'>{esc(h)}</th>"
                 for h in head)
    body = "".join(
        "<tr>" + "".join(f"<td style='padding:4px 8px;font-size:12px;border-bottom:1px solid #eee;'>{esc(c)}</td>" for c in r) + "</tr>"
        for r in rows)
    return f"<table cellpadding='0' cellspacing='0' width='100%' style='border-collapse:collapse;margin:6px 0;'><tr>{th}</tr>{body}</table>"


def _picks_html(sel: Optional[SectorSelection], failure_note: Optional[str]) -> str:
    """The stock-picks part of one sector card (or a notice when the picks could not be computed)."""
    label = "<p style='margin:12px 0 2px;font-size:11px;font-weight:700;color:#1a5276;letter-spacing:0.3px;'>STOCK PICKS</p>"
    if sel is None:
        return label + f"<p style='margin:4px 0;font-size:12px;color:#922b21;'>{esc(failure_note or 'Not available.')}</p>"
    plural = lambda n, word: f"{n} {word}{'s' if n != 1 else ''}"                      # noqa: E731
    meta = (f"<p style='margin:0 0 4px;font-size:11px;color:#777;'>up to {plural(sel.picks_allowed, 'pick')} "
            f"&nbsp;·&nbsp; {plural(sel.candidate_count, 'stock')} in the sector</p>")
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
    return label + meta + body


# ---------------------------------------------------------------------------
# Sector Signal card (the scout's original layout)
# ---------------------------------------------------------------------------

def _diversity_label(score: float) -> tuple[str, str]:
    if score >= 0.7:
        return "High source diversity", "#1a7a4a"
    if score >= 0.4:
        return "Moderate source diversity", "#b7770d"
    return "Low source diversity", "#922b21"


def _signal_card(sig: dict, sel: Optional[SectorSelection], failure_note: Optional[str]) -> str:
    conv = sig.get("convergence_type") or "News-Led"
    fg, bg = _CONV_COLOURS.get(conv, ("#1a5276", "#d6eaf8"))
    badge = (f"<span style='display:inline-block;padding:2px 8px;border-radius:3px;background:{bg};color:{fg};"
             f"font-size:11px;font-weight:700;letter-spacing:0.3px;'>{esc(_BADGE_LABEL.get(conv, conv))}</span>")

    bull = int(float(sig.get("confidence", 0)) * 100)
    bars = (f"<tr><td style='width:55px;font-size:11px;color:#27ae60;font-weight:700;'>BULL {bull}%</td>"
            f"<td><div style='width:{bull * 2}px;height:10px;background:#27ae60;border-radius:3px;'></div></td></tr>")
    if sig.get("bear_case_probability") is not None:
        bear = int(float(sig["bear_case_probability"]) * 100)
        bars += (f"<tr><td colspan='2' style='height:3px;'></td></tr>"
                 f"<tr><td style='width:55px;font-size:11px;color:#e74c3c;font-weight:700;'>BEAR {bear}%</td>"
                 f"<td><div style='width:{bear * 2}px;height:10px;background:#e74c3c;border-radius:3px;'></div></td></tr>")

    parts = []
    if sig.get("disruption_type"):
        parts.append(f"{esc(sig['disruption_type'])}")
    if sig.get("time_to_impact_weeks") is not None:
        parts.append(f"~{esc(sig['time_to_impact_weeks'])} week(s) to impact")
    if sig.get("disruption_strength") is not None:
        parts.append(f"Disruption strength: {int(float(sig['disruption_strength']) * 100)}%")
    if sig.get("source_diversity") is not None:
        text, colour = _diversity_label(float(sig["source_diversity"]))
        parts.append(f"<span style='font-size:11px;color:{colour};'>● {esc(text)} ({float(sig['source_diversity']):.0%})</span>")
    subtitle = " &nbsp;·&nbsp; ".join(parts)

    def line(label, value, colour="#222"):
        return f"<p style='margin:0 0 3px;font-size:12px;color:{colour};'><b>{label}:</b> {esc(value)}</p>" if value else ""

    retail = sig.get("retail_thesis", "")
    drivers = "".join(f"<li style='margin:2px 0;font-size:12px;color:#555;'>{esc(d)}</li>" for d in sig.get("conviction_drivers") or [])
    details = (line("Rationale", sig.get("rationale")) + line("Mechanism", sig.get("propagation"))
               + line("Kill switch", sig.get("invalidation_risk"))
               + line("Convergence", sig.get("convergence_note"), "#555")
               + (line("Reddit crowd", retail) if retail not in _NO_REDDIT else "")
               + line("Catalysts", " | ".join(sig.get("key_catalysts") or []))
               + line("Also watch", ", ".join(sig.get("correlated_sectors") or []))
               + (f"<p style='margin:6px 0 2px;font-size:12px;'><b>Evidence:</b></p>"
                  f"<ul style='margin:2px 0;padding-left:18px;'>{drivers}</ul>" if drivers else ""))
    return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px;border:1px solid #e0e0e0;border-radius:6px;border-left:4px solid {fg};background:#fafafa;">
  <tr><td style="padding:14px 18px;">
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:6px;"><tr>
      <td><p style="margin:0;font-size:16px;font-weight:700;color:#1a5276;">{esc(sig.get('sector'))}</p></td>
      <td align="right">{badge}</td></tr></table>
    <p style="margin:0 0 8px;font-size:12px;color:#777;">{subtitle}</p>
    <table cellpadding="0" cellspacing="0" style="margin-bottom:10px;">{bars}</table>
    {details}
    {_picks_html(sel, failure_note)}
  </td></tr>
</table>"""


def _signal_text(sig: dict, sel: Optional[SectorSelection], failure_note: Optional[str]) -> list[str]:
    conv = sig.get("convergence_type") or "News-Led"
    bear = sig.get("bear_case_probability")
    lines = [f"{sig.get('sector')} — bull {float(sig.get('confidence', 0)):.0%}"
             + (f", bear {float(bear):.0%}" if bear is not None else "") + f", {conv}"]
    for label, key in (("Rationale", "rationale"), ("Mechanism", "propagation"), ("Kill switch", "invalidation_risk"),
                       ("Convergence", "convergence_note")):
        if sig.get(key):
            lines.append(f"  {label}: {sig[key]}")
    if sig.get("key_catalysts"):
        lines.append("  Catalysts: " + " | ".join(sig["key_catalysts"]))
    if sig.get("correlated_sectors"):
        lines.append("  Also watch: " + ", ".join(sig["correlated_sectors"]))
    if sel is None:
        lines.append(f"  STOCK PICKS: {failure_note or 'not available'}")
    elif not sel.investable:
        lines.append("  STOCK PICKS: no stock in the Sector Map has this as its Primary Sector.")
    elif not sel.picks:
        lines.append("  STOCK PICKS: no eligible stock this week.")
    for p in (sel.picks if sel else []):
        lines.append(f"  PICK {p['ticker']} ({p['company']}): {p['swing_setup']}, grade {p['setup_grade']}, risk {p['risk_score']}/10, "
                     f"stop-loss {p['stop_loss_pct']:.1f}%, max position {p['max_position_pct']:.1f}%, "
                     f"close {price_text(p['close'], p['ticker'])} ({market_of(p['ticker'])})")
    for r in (sel.runners_up if sel else []):
        lines.append(f"  not picked: {r['ticker']} ({r['company']}) — {r['reason']}")
    return lines + [""]


# ---------------------------------------------------------------------------
# The email
# ---------------------------------------------------------------------------

def build_report_email(
        week:         str,
        signal_ts:    datetime,
        payload:      dict,
        selections:   Optional[list[SectorSelection]] = None,
        prices:       Optional[PriceFetchResult] = None,
        source_uri:   Optional[str] = None,
        snapshot_key: Optional[str] = None,
        map_age_days: Optional[int] = None,
        error:        Optional[str] = None,
        failed_key:   Optional[str] = None,
        ) -> tuple[str, str, str]:
    """
    Returns (subject, html_body, text_body): the whole week in one email.

    The scout's macro regime and one card per Sector Signal (strongest first),
    each with its Picks and the best runners-up.  Data warnings go at the foot.
    When `selections` is None (the picks step failed, `error` says why) every
    card carries a failure notice instead of picks and a banner sits at the top.
    """
    signals = sorted(payload.get("signals") or [], key=lambda s: -float(s.get("confidence", 0)))
    by_sector = {s.sector: s for s in selections or []}
    failed = selections is None
    n_picks = sum(len(s.picks) for s in selections or [])
    plural = lambda n, word: f"{n} {word}{'s' if n != 1 else ''}"                      # noqa: E731
    failure_note = f"Stock picks unavailable this week: {error}" if failed else None

    status = "stock picks FAILED" if failed else plural(n_picks, "pick")
    subject = f"Sector Scout — {plural(len(signals), 'signal')} · {status} · {week}"
    warnings = [] if failed else build_warnings(prices, map_age_days)

    facts = [esc(signal_ts.strftime("%d %b %Y %H:%M UTC")), plural(len(signals), "swing signal") + " detected"]
    if not failed:
        facts += [cutoff_text(prices), f"{len(prices.data)} of {len(prices.requested)} stocks analysed"]
    facts += [esc(x) for x in (source_uri, snapshot_key) if x]

    macro = payload.get("macro")
    macro_html = (f"<div style='background:#eaf2f8;border-left:4px solid #2980b9;padding:10px 14px;border-radius:4px;margin-bottom:22px;'>"
                  f"<p style='margin:0;font-size:12px;font-weight:700;color:#2980b9;'>MACRO REGIME</p>"
                  f"<p style='margin:4px 0 0;font-size:13px;color:#333;'>{esc(macro)}</p></div>") if macro else ""
    banner_html = (f"<div style='background:#fdedec;border-left:4px solid #922b21;padding:10px 14px;border-radius:4px;margin-bottom:18px;'>"
                   f"<p style='margin:0 0 4px;font-size:12px;font-weight:700;color:#922b21;'>STOCK PICKS FAILED</p>"
                   f"<p style='margin:0;font-size:12px;color:#555;'>{esc(error)}. The sector report below is complete; only the "
                   f"stock picks are missing this week."
                   f"{(' Details: ' + esc(failed_key)) if failed_key else ''}</p></div>") if failed else ""
    cards = "\n".join(_signal_card(s, by_sector.get(s.get("sector")), failure_note) for s in signals) or \
        "<p style='font-size:13px;color:#777;'>The sector scout reported no signals.</p>"
    warn_html = ("<div style='background:#fef9e7;border-left:4px solid #f1c40f;padding:10px 14px;border-radius:4px;margin-top:8px;'>"
                 "<p style='margin:0 0 4px;font-size:12px;font-weight:700;color:#9a7d0a;'>DATA WARNINGS</p><ul style='margin:0;padding-left:18px;'>"
                 + "".join(f"<li style='font-size:12px;color:#555;margin:2px 0;'>{esc(w)}</li>" for w in warnings)
                 + "</ul></div>") if warnings else ""
    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:720px;margin:0 auto;padding:20px;color:#222;background:#fff;">
  <div style="background:#1a5276;padding:18px 22px;border-radius:6px 6px 0 0;">
    <h1 style="margin:0;font-size:20px;color:#fff;letter-spacing:0.5px;">📈 Sector Scout</h1>
    <p style="margin:4px 0 0;font-size:12px;color:#aed6f1;">{' &nbsp;·&nbsp; '.join(facts)}</p>
  </div>
  <div style="border:1px solid #e0e0e0;border-top:none;padding:20px 22px;border-radius:0 0 6px 6px;">
    {banner_html}
    {macro_html}
    {cards}
    {warn_html}
    <hr style="border:none;border-top:1px solid #eee;margin:18px 0;">
    <p style="font-size:11px;color:#aaa;margin:0;">Sector signals from the Sector Scout (OpenRouter LLM pipeline); stock picks are rule-based
      from daily-bar trend and risk analysis within the signalled sectors. Position sizes follow a 2% risk rule.
      Not investment advice. Always validate independently.</p>
  </div>
</body></html>"""

    lines = [f"Sector Scout — {week}", " · ".join([signal_ts.strftime("%d %b %Y %H:%M UTC"), plural(len(signals), "signal")]
                                                     + ([cutoff_text(prices),
                                                         f"{len(prices.data)} of {len(prices.requested)} stocks analysed"] if not failed else []))]
    if failed:
        lines += ["", f"STOCK PICKS FAILED: {error}", "The sector report below is complete; only the stock picks are missing this week."]
        if failed_key:
            lines.append(f"Details: {failed_key}")
    if macro:
        lines += ["", f"MACRO REGIME: {macro}"]
    lines.append("")
    for s in signals:
        lines += _signal_text(s, by_sector.get(s.get("sector")), failure_note)
    if warnings:
        lines += ["DATA WARNINGS"] + [f"  - {w}" for w in warnings] + [""]
    lines.append("Not investment advice. Always validate independently.")
    return subject, html_body, "\n".join(lines)


def build_failure_email(week: str, source_key: str, error: str, failed_key: Optional[str] = None) -> tuple[str, str, str]:
    """
    Short (subject, html, text) email for a week where not even the sector report can be shown
    (the scout's result could not be read, or has no signals).
    """
    subject = f"Sector Scout FAILED — {week}"
    text = (f"The stock-selection step failed for {week} and the scout's result could not be turned into a report.\n\n"
            f"Reason: {error}\nSector scout result: {source_key}\n"
            + (f"Details saved to: {failed_key}\n" if failed_key else ""))
    body = "".join(f"<p style='font-size:13px;margin:6px 0;'>{esc(line)}</p>" for line in text.split("\n") if line)
    html_body = (f"<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body style='font-family:Arial,sans-serif;max-width:640px;"
                 f"margin:0 auto;padding:20px;'><div style='background:#922b21;padding:14px 20px;border-radius:6px 6px 0 0;'>"
                 f"<h1 style='margin:0;font-size:18px;color:#fff;'>⚠ Sector Scout failed</h1></div>"
                 f"<div style='border:1px solid #e0e0e0;border-top:none;padding:16px 20px;'>{body}</div></body></html>")
    return subject, html_body, text
