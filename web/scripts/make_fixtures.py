"""
Build the website's fixture data (web/fixtures/) with the real Paper Portfolio code.

    python web/scripts/make_fixtures.py

Four made-up weeks go through stock_selection_lambda/portfolio_step.py exactly as
the Lambda would run them, so the fixtures always match docs/site-data.md:

  2026-W44  opening week: three Picks bought (one US), full Run Trace
  2026-W45  a stop is hit, a holding is picked again, new buys; one feed fails,
            the Reddit pass is throttled and the fallback model answers the consolidation pass
  2026-W46  the scout never succeeded: a no-trade week with no Run Trace
  2026-W47  a scout result from before the trace block existed (scout.trace is null)

The tickers and prices are illustrative, not real market data.
"""
import io
import json
import random
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stock_selection_lambda"))
sys.path.insert(0, str(ROOT / "financial_market_news_analyzer"))

import config  # noqa: E402  (the scout's feeds, models and Reddit requests)
import portfolio_step as ps  # noqa: E402
from lse_stock_analysis.selection import picks_allowed  # noqa: E402

OUT = ROOT / "web" / "fixtures"

# ticker -> (company, market, sector)
STOCKS = {
    "SSE.L": ("SSE", "LSE", "Electricity & Grid"), "NG.L": ("National Grid", "LSE", "Electricity & Grid"),
    "AEP": ("American Electric Power", "US", "Electricity & Grid"), "DUK": ("Duke Energy", "US", "Electricity & Grid"),
    "SHEL.L": ("Shell", "LSE", "Integrated Oil & Gas"), "ITH.L": ("Ithaca Energy", "LSE", "Integrated Oil & Gas"),
    "XOM": ("Exxon Mobil", "US", "Integrated Oil & Gas"), "NVDA": ("NVIDIA", "US", "AI Infrastructure"),
    "VRT": ("Vertiv", "US", "AI Infrastructure"), "BKG.L": ("Berkeley Group", "LSE", "Housebuilders"),
    "PSN.L": ("Persimmon", "LSE", "Housebuilders"), "TW.L": ("Taylor Wimpey", "LSE", "Housebuilders"),
    "LLOY.L": ("Lloyds Banking Group", "LSE", "UK Retail Banks"), "BARC.L": ("Barclays", "LSE", "UK Retail Banks"),
    "ULVR.L": ("Unilever", "LSE", "Consumer Staples & FMCG"), "PG": ("Procter & Gamble", "US", "Consumer Staples & FMCG"),
}
GRADES = {"SSE.L": "A", "NG.L": "B", "AEP": "A", "DUK": "C", "SHEL.L": "C", "ITH.L": "B", "XOM": "C", "NVDA": "C",
          "VRT": "C", "BKG.L": "A", "PSN.L": "B", "TW.L": "C", "LLOY.L": "B", "BARC.L": "A", "ULVR.L": "C", "PG": "B"}

CLOSES = {
    "2026-W44": {"SSE.L": 2627.0, "NG.L": 1050.0, "AEP": 130.21, "DUK": 118.4, "SHEL.L": 2650.0, "ITH.L": 302.8, "XOM": 118.2,
                 "NVDA": 181.5, "VRT": 152.0, "BKG.L": 4040.0, "PSN.L": 1402.0, "TW.L": 108.6, "LLOY.L": 58.2, "BARC.L": 281.0,
                 "ULVR.L": 4510.0, "PG": 158.0},
    "2026-W45": {"SSE.L": 2690.0, "NG.L": 1058.0, "AEP": 132.9, "DUK": 119.0, "SHEL.L": 2602.0, "ITH.L": 281.0, "XOM": 116.0,
                 "NVDA": 186.0, "VRT": 149.5, "BKG.L": 4088.0, "PSN.L": 1415.0, "TW.L": 110.2, "LLOY.L": 58.9, "BARC.L": 284.0,
                 "ULVR.L": 4498.0, "PG": 157.1},
    "2026-W47": {"SSE.L": 2745.0, "NG.L": 1071.0, "AEP": 131.7, "DUK": 120.3, "SHEL.L": 2580.0, "ITH.L": 276.0, "XOM": 115.3,
                 "NVDA": 179.0, "VRT": 145.0, "BKG.L": 4190.0, "PSN.L": 1388.0, "TW.L": 111.0, "LLOY.L": 60.1, "BARC.L": 290.5,
                 "ULVR.L": 4520.0, "PG": 159.3},
}
FX = {"2026-W44": 1.3379, "2026-W45": 1.3302, "2026-W47": 1.3415}
CUTOFF = {"2026-W44": "2026-10-30", "2026-W45": "2026-11-06", "2026-W47": "2026-11-20"}
RUN_AT = {"2026-W44": datetime(2026, 10, 31, 6, 11, tzinfo=timezone.utc),
          "2026-W45": datetime(2026, 11, 7, 6, 9, tzinfo=timezone.utc),
          "2026-W47": datetime(2026, 11, 21, 6, 14, tzinfo=timezone.utc)}

SIGNALS = {
    "2026-W44": [
        {"sector": "Electricity & Grid", "confidence": 0.71, "convergence_type": "News-Led", "bear_case_probability": 0.25,
         "disruption_type": "Policy / Regulation", "disruption_strength": 0.62, "time_to_impact_weeks": 3, "source_diversity": 0.72,
         "rationale": "Grid investment plans on both sides of the Atlantic are being brought forward as data-centre power demand outruns supply.",
         "propagation": "Regulated asset bases grow, lifting allowed returns for network owners.",
         "invalidation_risk": "A delay to the regulator's final determinations.",
         "convergence_note": "Strong news flow; Reddit is quiet on utilities.", "key_catalysts": ["Regulator final determinations", "Capacity auction results"],
         "correlated_sectors": ["Renewables & Clean Energy", "Data Centres & Digital Infrastructure"],
         "conviction_drivers": ["(headline text, never published)"], "retail_thesis": "(Reddit paraphrase, never published)"},
        {"sector": "AI Infrastructure", "confidence": 0.62, "convergence_type": "News-Led", "bear_case_probability": 0.4,
         "disruption_type": "Technology Shift", "disruption_strength": 0.55, "time_to_impact_weeks": 4, "source_diversity": 0.51,
         "rationale": "Hyperscaler capital spending guidance was raised again.", "propagation": "Orders flow to power and cooling suppliers.",
         "invalidation_risk": "Capex guidance cut at the next earnings round.", "key_catalysts": ["Hyperscaler earnings"],
         "correlated_sectors": ["Semiconductors & EDA"]},
        {"sector": "Integrated Oil & Gas", "confidence": 0.55, "convergence_type": "News-Led", "bear_case_probability": 0.45,
         "disruption_type": "Supply Shock", "disruption_strength": 0.48, "time_to_impact_weeks": 2, "source_diversity": 0.44,
         "rationale": "Supply discipline from producers keeps crude prices firm.", "propagation": "Upstream margins widen.",
         "invalidation_risk": "A surprise output increase.", "key_catalysts": ["Producer group meeting"], "correlated_sectors": ["Oil Field Services"]},
    ],
    "2026-W45": [
        {"sector": "Housebuilders", "confidence": 0.82, "convergence_type": "Convergent", "bear_case_probability": 0.2,
         "disruption_type": "Monetary Policy", "disruption_strength": 0.66, "time_to_impact_weeks": 3, "source_diversity": 0.8,
         "rationale": "Mortgage rates are falling faster than expected after the central bank's guidance shifted.",
         "propagation": "Cheaper mortgages lift reservation rates and pricing.", "invalidation_risk": "Sticky inflation data.",
         "convergence_note": "News and Reddit agree.", "key_catalysts": ["Mortgage approvals data"], "correlated_sectors": ["UK Retail Banks"]},
        {"sector": "Electricity & Grid", "confidence": 0.68, "convergence_type": "Convergent", "bear_case_probability": 0.28,
         "disruption_type": "Policy / Regulation", "disruption_strength": 0.6, "time_to_impact_weeks": 3, "source_diversity": 0.7,
         "rationale": "Follow-through on grid spending plans.", "propagation": "Rate base growth.", "invalidation_risk": "Political pushback on bills.",
         "key_catalysts": ["Network price controls"], "correlated_sectors": ["Water"]},
    ],
    "2026-W47": [
        {"sector": "Housebuilders", "confidence": 0.70, "convergence_type": "News-Led", "rationale": "Housing data stays firm.",
         "propagation": "Volumes recover.", "invalidation_risk": "Rate expectations reverse."},
        {"sector": "UK Retail Banks", "confidence": 0.60, "convergence_type": "Divergent", "rationale": "Net interest margins hold up.",
         "propagation": "Earnings upgrades.", "invalidation_risk": "Faster rate cuts."},
    ],
}
PICKS = {"2026-W44": ["AEP", "SSE.L", "ITH.L"], "2026-W45": ["BKG.L", "PSN.L", "SSE.L"], "2026-W47": ["BKG.L", "BARC.L"]}
RUNNERS_UP = {
    "2026-W44": {"Electricity & Grid": [("NG.L", "National Grid", "ranked below the pick limit"), ("DUK", "Duke Energy", "setup grade C (below B)")],
                 "AI Infrastructure": [("NVDA", "NVIDIA", "RSI 74 is overbought"), ("VRT", "Vertiv", "trend is sideways")],
                 "Integrated Oil & Gas": [("SHEL.L", "Shell", "setup grade C (below B)"), ("XOM", "Exxon Mobil", "no swing setup")]},
    "2026-W45": {"Housebuilders": [("TW.L", "Taylor Wimpey", "setup grade C (below B)")],
                 "Electricity & Grid": [("AEP", "American Electric Power", "ranked below the pick limit")]},
    "2026-W47": {},
}


def snapshot(week: str) -> pd.DataFrame:
    best = {s["sector"]: s for s in SIGNALS[week]}
    rows = []
    for t, (company, market, sector) in STOCKS.items():
        sig = best.get(sector)
        grade = GRADES[t]
        rows.append({
            "week": week, "ticker": t, "market": market, "currency": "USD" if market == "US" else "GBp", "company": company,
            "primary_sector": sector, "status": "ok", "close": CLOSES[week][t], "stale": False, "price_anomaly": False,
            "unit_fixed": 0, "price_cutoff": CUTOFF[week], "is_candidate": sig is not None, "is_pick": t in PICKS[week],
            "signal_confidence": sig["confidence"] if sig else None, "signal_convergence_type": sig["convergence_type"] if sig else None,
            "setup_grade": grade, "risk_score": {"A": 3, "B": 4, "C": 6}[grade],
            "max_position_size_pct": 20.0 if grade == "A" else 14.0, "atr_stop_pct": 5.5 if market == "US" else 6.2,
            "swing_setup": {"A": "breakout", "B": "pullback", "C": "none"}[grade], "tradeable": grade != "C",
        })
    return pd.DataFrame(rows)


def scout_trace(week: str, rnd: random.Random) -> dict:
    feeds = []
    for f in config.RSS_FEEDS:
        fetched = rnd.randint(8, 60)
        failed = week == "2026-W45" and f["name"] == "Bloomberg Markets"
        feeds.append({"name": f["name"], "fetched": 0 if failed else fetched, "ok": not failed,
                      "error": "network error" if failed else None, "fresh": None, "kept": None})
    feeds.append({"name": "HL Weekly Outlook", "fetched": 6, "ok": True, "error": None, "fresh": None, "kept": None})
    fetched = sum(f["fetched"] for f in feeds)
    dupes, stale = fetched // 14, fetched // 6
    fresh_total = fetched - dupes - stale
    kept_total = min(500, fresh_total)
    for f in feeds:
        f["fresh"] = round(f["fetched"] * fresh_total / fetched)
        f["kept"] = round(f["fresh"] * kept_total / fresh_total)
    requests = []
    for r in config.REDDIT_REQUESTS:
        label = "+".join(r["subreddits"]) + "/" + r.get("sort", "hot")
        throttled = week == "2026-W45" and r["subreddits"][0] in ("wallstreetbets", "CanadianInvestor")
        requests.append({"label": label, "status": "throttled" if throttled else "ok", "posts": 0 if throttled else 100,
                         "retried": throttled})
    ok = sum(r["status"] == "ok" for r in requests)
    models = list(config.OPENROUTER_MODELS)
    fallback_pass = "consolidation" if week == "2026-W45" else None
    llm = {name: {"models": models, "model_used": models[-1] if name == fallback_pass else models[0], "ok": True,
                  "seconds": secs, "fallback": name == fallback_pass}
           for name, secs in (("news", 38.4), ("reddit", 21.7), ("consolidation", 17.9))}
    n = len(SIGNALS[week])
    return {
        "trigger": "WeeklyTrigger", "feeds": feeds,
        "headlines": {"fetched": fetched, "duplicates": dupes, "stale": stale, "fresh": fresh_total, "cap": 500,
                      "kept": kept_total, "max_age_days": 7},
        "reddit": {"planned": len(requests), "succeeded": ok, "unique_posts": ok * 85, "fresh": ok * 70,
                   "kept": min(120, ok * 70), "cap": 120, "subreddits": 14, "seconds": 402.5, "requests": requests},
        "llm": llm,
        "signals": {"news_raw": n + 3, "news_passed": n + 1, "news_llm_ok": True, "min_confidence": 0.5, "min_disruption": 0.4,
                    "reddit_posts": min(120, ok * 70), "reddit_signals": 2, "reddit_llm_ok": True, "consolidated": n,
                    "consolidator_ok": True, "reddit_skipped": False},
        "timings": {"news_fetch": 64.2, "reddit_wait": 341.0, "total": 470.8},
    }


class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    def get_paginator(self, name):
        objects = self.objects

        class Pages:
            def paginate(self, Bucket, Prefix):
                return [{"Contents": [{"Key": k} for k in sorted(objects) if k.startswith(Prefix)]}]
        return Pages()


def main() -> int:
    rnd = random.Random(44)
    s3 = FakeS3()
    store = ps.Store(s3, "fixtures")
    for week in ("2026-W44", "2026-W45", "2026-W47"):
        now = lambda w=week: RUN_AT[w]                                       # noqa: E731
        snap = snapshot(week)
        source_key = f"successful/{week}/response_{RUN_AT[week]:%Y%m%dT%H%M%SZ}.json"
        ps.run_week(store, week, snap, FX[week], fetch_fx=lambda on: None, source_key=source_key, now=now)
        payload = {"timestamp": f"{RUN_AT[week]:%Y%m%dT%H%M%SZ}", "prompt_version": "2026-10-31.2", "models": list(config.OPENROUTER_MODELS),
                   "macro": {"2026-W44": "Risk-on: easing inflation and firm earnings; energy and grid spending lead.",
                             "2026-W45": "Rate-cut expectations build; rate-sensitive sectors bid.",
                             "2026-W47": "Mixed: growth steady, rates drifting lower."}[week],
                   "signals": SIGNALS[week], "trace": None if week == "2026-W47" else scout_trace(week, rnd)}
        selections = [SimpleNamespace(sector=s["sector"], picks_allowed=picks_allowed(s["confidence"], s["convergence_type"]),
                                      runners_up=[{"ticker": t, "company": c, "reason": r}
                                                  for t, c, r in RUNNERS_UP[week].get(s["sector"], [])])
                      for s in SIGNALS[week]]
        trace = ps.build_trace(week, payload, snap, selections, email_sent=True, now=now)
        ps.publish_site(store, week, trace, now=now)

    if OUT.exists():
        shutil.rmtree(OUT)
    for key, body in s3.objects.items():
        if key.startswith("site/"):
            path = OUT / key[len("site/"):]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(json.loads(body), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Fixtures written to {OUT}: {sorted(p.relative_to(OUT).as_posix() for p in OUT.rglob('*.json'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
