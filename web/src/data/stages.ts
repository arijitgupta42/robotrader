// The stage model: one entry per stage of the weekly pipeline, in the order the code runs them.
// It is the single source of truth for both views: the plain list and the 3D machine read the
// same names, code references, descriptions and per-week numbers from here.
import { gbp, int, pct } from "./format";
import type { HistoryPoint, Note, PortfolioData, Trade, WeekTrace } from "./types";

export type StageStatus = "ok" | "warn" | "fail" | "idle";

export interface Fact {
  label: string;
  value: string;
}

export interface StageReading {
  status: StageStatus;
  headline: string;          // one sentence for this week
  facts: Fact[];
  items?: { label: string; value: string; status?: StageStatus }[];   // per-feed, per-pass, per-sector detail
}

export interface CodeRef {
  file: string;
  symbol: string;
}

/** What a stage reads for one week. */
export interface WeekContext {
  week: string;
  trace: WeekTrace | null;
  history: HistoryPoint | null;
  trades: Trade[];
  notes: Note[];
  portfolio: PortfolioData | null;
}

export type StageId =
  | "schedule" | "feeds" | "reddit" | "sieve" | "llm" | "signals"
  | "handoff" | "prices" | "analysis" | "selection" | "portfolio" | "email";

export interface Stage {
  id: StageId;
  title: string;
  machinePart: string;       // what it is in the 3D machine
  component: string;         // what it really is
  lambda: "sector-scout" | "stock-selection";
  code: CodeRef[];
  description: string;
  read: (ctx: WeekContext) => StageReading;
}

const NO_DETAIL = "No detail recorded for this stage this week.";

function idle(headline = NO_DETAIL): StageReading {
  return { status: "idle", headline, facts: [] };
}

/** The scout never produced a result: nothing downstream of the schedule ran. */
function scoutMissing(ctx: WeekContext): boolean {
  return ctx.trace === null && ctx.history !== null && !ctx.history.scout_ok;
}

function notRun(ctx: WeekContext, what = "Did not run: the scout had no successful result this week."): StageReading {
  return scoutMissing(ctx) ? { status: "fail", headline: what, facts: [] } : idle();
}

function scoutTrace(ctx: WeekContext) {
  return ctx.trace?.scout.trace ?? null;
}

export function stageContext(
  week: string, trace: WeekTrace | null, portfolio: PortfolioData | null, trades: Trade[], notes: Note[],
): WeekContext {
  return {
    week, trace, portfolio,
    history: portfolio?.history.find((h) => h.week === week) ?? null,
    trades: trades.filter((t) => t.week === week),
    notes: notes.filter((n) => n.week === week),
  };
}

const PASS_NAMES: Record<string, string> = { news: "News", reddit: "Reddit", consolidation: "Consolidation" };

export const STAGES: Stage[] = [
  {
    id: "schedule",
    title: "Weekly schedule",
    machinePart: "Clock and escapement",
    component: "EventBridge Scheduler: a weekly trigger (Saturday 06:00 UTC) and an hourly retry until the scout succeeds",
    lambda: "sector-scout",
    code: [{ file: "infra/scout.tf", symbol: "aws_scheduler_schedule.scout_weekly / scout_retry" },
           { file: "financial_market_news_analyzer/lambda_handler.py", symbol: "handler" }],
    description: "Every Saturday morning a schedule starts the sector scout. If a run fails, an hourly retry tries again until one succeeds that week.",
    read: (ctx) => {
      const t = scoutTrace(ctx);
      if (scoutMissing(ctx)) return { status: "fail", headline: "The scout ran but never succeeded this week.", facts: [] };
      if (!t?.trigger) return ctx.trace ? idle("Started on schedule (the trigger was not recorded).") : idle();
      const text = t.trigger === "WeeklyTrigger" ? "Started by the weekly schedule."
        : t.trigger === "HourlyRetry" ? "Started by an hourly retry after an earlier attempt failed."
        : "Started by hand.";
      return { status: t.trigger === "HourlyRetry" ? "warn" : "ok", headline: text, facts: [{ label: "Trigger", value: t.trigger }] };
    },
  },
  {
    id: "feeds",
    title: "News feeds",
    machinePart: "Intake hoppers",
    component: "About 40 RSS feeds plus a broker's weekly outlook page, fetched one after another",
    lambda: "sector-scout",
    code: [{ file: "financial_market_news_analyzer/news_fetcher.py", symbol: "fetch_rss, fetch_hl_weekly_outlook" },
           { file: "financial_market_news_analyzer/config.py", symbol: "RSS_FEEDS" }],
    description: "Business and markets news is pulled from about 40 public feeds around the world. Each feed is a hopper; a feed that fails jams.",
    read: (ctx) => {
      const feeds = scoutTrace(ctx)?.feeds;
      if (!feeds?.length) return notRun(ctx);
      const ok = feeds.filter((f) => f.ok);
      const fetched = feeds.reduce((s, f) => s + f.fetched, 0);
      const failed = feeds.filter((f) => !f.ok);
      return {
        status: ok.length === 0 ? "fail" : failed.length ? "warn" : "ok",
        headline: `${ok.length} of ${feeds.length} sources answered with ${int(fetched)} headlines.`,
        facts: [{ label: "Sources answered", value: `${ok.length} of ${feeds.length}` }, { label: "Headlines fetched", value: int(fetched) },
                ...(failed.length ? [{ label: "Failed", value: failed.map((f) => `${f.name} (${f.error ?? "error"})`).join(", ") }] : [])],
        items: [...feeds].sort((a, b) => Number(a.ok) - Number(b.ok) || b.fetched - a.fetched).map((f) => ({   // failures first
          label: f.name, value: f.ok ? `${int(f.fetched)} fetched · ${int(f.kept ?? 0)} kept` : (f.error ?? "failed"),
          status: f.ok ? "ok" : "fail",
        })),
      };
    },
  },
  {
    id: "reddit",
    title: "Reddit pass",
    machinePart: "Drip valve on a side pipe",
    component: "Public Reddit RSS, about one request a minute, collected in a background thread while the news is analysed",
    lambda: "sector-scout",
    code: [{ file: "financial_market_news_analyzer/reddit_fetcher.py", symbol: "collect_reddit_posts" },
           { file: "financial_market_news_analyzer/config.py", symbol: "REDDIT_REQUESTS" }],
    description: "Retail investors' posts from a handful of subreddits. Reddit only allows about one request a minute, so this drips in slowly alongside the news.",
    read: (ctx) => {
      const r = scoutTrace(ctx)?.reddit;
      if (!r) {
        if (scoutTrace(ctx)?.signals?.reddit_skipped) return idle("Skipped this week.");
        return notRun(ctx);
      }
      const planned = r.planned ?? r.requests?.length ?? 0;
      const ok = r.succeeded ?? 0;
      const throttled = (r.requests ?? []).filter((q) => q.status === "throttled").length;
      return {
        status: ok === 0 ? "fail" : ok < planned ? "warn" : "ok",
        headline: `${ok} of ${planned} requests returned posts; ${int(r.kept ?? 0)} posts kept.`,
        facts: [{ label: "Requests with posts", value: `${ok} of ${planned}` }, { label: "Throttled (HTTP 429)", value: int(throttled) },
                { label: "Posts kept", value: `${int(r.kept ?? 0)} (cap ${int(r.cap ?? 0)})` },
                { label: "Time", value: `${Math.round(r.seconds ?? 0)} s` }],
        items: (r.requests ?? []).map((q) => ({
          label: `r/${q.label}`, value: q.status === "ok" ? `${q.posts} posts${q.retried ? " (after a retry)" : ""}` : q.status,
          status: q.status === "ok" ? "ok" : q.status === "empty" ? "warn" : "fail",
        })),
      };
    },
  },
  {
    id: "sieve",
    title: "Sieve",
    machinePart: "Sieve",
    component: "Duplicates removed, headlines older than 7 days dropped, then a balanced cap so no one source dominates",
    lambda: "sector-scout",
    code: [{ file: "financial_market_news_analyzer/news_fetcher.py", symbol: "collect_headlines, _cap_balanced" }],
    description: "The same story from several outlets is kept once, old news is dropped, and sources take turns until the cap is reached so a busy feed cannot crowd out a quiet one.",
    read: (ctx) => {
      const h = scoutTrace(ctx)?.headlines;
      if (!h) return notRun(ctx);
      return {
        status: h.kept > 0 ? "ok" : "fail",
        headline: `${int(h.fetched)} in, ${int(h.duplicates)} duplicates and ${int(h.stale)} stale removed, ${int(h.kept)} kept.`,
        facts: [{ label: "In", value: int(h.fetched) }, { label: "Duplicates", value: int(h.duplicates) },
                { label: `Older than ${h.max_age_days} days`, value: int(h.stale) }, { label: "Kept", value: `${int(h.kept)} (cap ${int(h.cap)})` }],
      };
    },
  },
  {
    id: "llm",
    title: "LLM passes",
    machinePart: "Engine block: three cylinders and a spare",
    component: "Three OpenRouter LLM passes (news, Reddit, consolidation); a fallback model answers if the primary fails",
    lambda: "sector-scout",
    code: [{ file: "financial_market_news_analyzer/llm_analyzer.py", symbol: "analyse_headlines" },
           { file: "financial_market_news_analyzer/reddit_analyzer.py", symbol: "analyse_reddit_posts" },
           { file: "financial_market_news_analyzer/consolidator.py", symbol: "consolidate_signals" }],
    description: "A language model reads the headlines, then the Reddit posts, then combines both into this week's Sector Signals. Each pass is a cylinder; the spare cylinder only fires when the primary model fails and the fallback model answers.",
    read: (ctx) => {
      const llm = scoutTrace(ctx)?.llm;
      const passes = llm ? (["news", "reddit", "consolidation"] as const).filter((p) => llm[p]) : [];
      if (!passes.length) return notRun(ctx);
      const failed = passes.filter((p) => !llm![p]!.ok);
      const fallback = passes.filter((p) => llm![p]!.fallback);
      const headline = failed.length ? `${failed.map((p) => PASS_NAMES[p]).join(", ")} pass failed.`
        : fallback.length ? `The fallback model answered the ${fallback.map((p) => PASS_NAMES[p].toLowerCase()).join(" and ")} pass.`
        : `${passes.length} passes, all answered by the primary model.`;
      return {
        status: failed.length ? "fail" : fallback.length ? "warn" : "ok",
        headline,
        facts: [{ label: "Models", value: (llm![passes[0]]!.models ?? []).join(" → ") }],
        items: passes.map((p) => {
          const x = llm![p]!;
          return { label: `${PASS_NAMES[p]} pass`, value: x.ok ? `${x.model_used} · ${x.seconds.toFixed(0)} s${x.fallback ? " · fallback" : ""}` : "no model answered",
                   status: !x.ok ? "fail" : x.fallback ? "warn" : "ok" };
        }),
      };
    },
  },
  {
    id: "signals",
    title: "Sector Signals",
    machinePart: "Stamping press with 31 dies",
    component: "The scout's weekly bullish calls, one per Sector, from a fixed list of 31 Sectors",
    lambda: "sector-scout",
    code: [{ file: "financial_market_news_analyzer/consolidator.py", symbol: "ConsolidatedSignal" },
           { file: "financial_market_news_analyzer/config.py", symbol: "SECTORS" }],
    description: "Each of the 31 Sectors has a die. A die stamps when the scout makes a bullish call on that Sector, with its confidence and whether news and Reddit agree (the convergence type).",
    read: (ctx) => {
      const sigs = ctx.trace?.scout.signals ?? [];
      if (!sigs.length) return notRun(ctx);
      const sorted = [...sigs].sort((a, b) => b.confidence - a.confidence);
      return {
        status: "ok",
        headline: `${sigs.length} Sector Signal${sigs.length === 1 ? "" : "s"}: ${sorted.map((s) => `${s.sector} ${pct(s.confidence)}`).join(", ")}.`,
        facts: ctx.trace?.scout.macro ? [{ label: "Macro regime", value: ctx.trace.scout.macro }] : [],
        items: sorted.map((s) => ({ label: s.sector, value: `${pct(s.confidence)} · ${s.convergence_type}`, status: "ok" })),
      };
    },
  },
  {
    id: "handoff",
    title: "Hand-off to stock selection",
    machinePart: "Pneumatic tube",
    component: "The scout's result is saved to S3 (successful/<week>/), and that write starts the stock-selection Lambda",
    lambda: "stock-selection",
    code: [{ file: "infra/main.tf", symbol: "aws_s3_bucket_notification.successful_results" },
           { file: "stock_selection_lambda/handler.py", symbol: "process_record" }],
    description: "The scout saves its result as a file in cloud storage. Saving it fires the second half of the machine, which works out which stocks to pick.",
    read: (ctx) => {
      if (!ctx.trace) return notRun(ctx);
      const ts = ctx.trace.scout.timestamp;
      const when = ts && /^\d{8}T\d{6}Z$/.test(ts) ? `${ts.slice(9, 11)}:${ts.slice(11, 13)} UTC` : null;
      return {
        status: "ok",
        headline: `Result saved${when ? ` at ${when}` : ""} and handed to stock selection.`,
        facts: [{ label: "Prompt version", value: ctx.trace.scout.prompt_version ?? "unknown" }],
      };
    },
  },
  {
    id: "prices",
    title: "Prices",
    machinePart: "Pump filling 853 tanks",
    component: "Daily prices for every Universe stock (FTSE 350 and S&P 500) from Yahoo Finance, with retries",
    lambda: "stock-selection",
    code: [{ file: "lse_stock_analysis/prices.py", symbol: "fetch_universe_prices, clean_prices" }],
    description: "Prices are downloaded for all 853 stocks the system can pick from. Each stock is a tank; a stale or suspicious price leaves its tank flagged.",
    read: (ctx) => {
      const p = ctx.trace?.prices;
      if (!p) return notRun(ctx, "Did not run: there was no scout result to act on.");
      const problems = p.no_data + p.stale + p.anomalies + p.short_history;
      return {
        status: p.returned / Math.max(p.requested, 1) < 0.8 ? "fail" : problems ? "warn" : "ok",
        headline: `Prices for ${int(p.returned)} of ${int(p.requested)} stocks${problems ? `; ${int(problems)} with a data problem` : ""}.`,
        facts: [{ label: "Returned", value: `${int(p.returned)} of ${int(p.requested)}` }, { label: "Stale", value: int(p.stale) },
                { label: "Price Anomalies", value: int(p.anomalies) }, { label: "Too little history", value: int(p.short_history) },
                ...Object.entries(p.cutoffs).map(([m, d]) => ({ label: `${m} prices to`, value: d }))],
      };
    },
  },
  {
    id: "analysis",
    title: "Trend and risk analysis",
    machinePart: "Gauges on the tanks",
    component: "Rule-based trend and risk agents: indicators, swing setup, Setup Grade A/B/C, stop distance, position size",
    lambda: "stock-selection",
    code: [{ file: "lse_stock_analysis/selection.py", symbol: "analyse_universe" },
           { file: "lse_stock_analysis/agents/", symbol: "TrendAnalysisAgent, RiskScoringAgent" }],
    description: "Every stock's price history is measured: trend, momentum, volatility, and whether it shows a swing-trading setup. Each gets a grade from A (best) to C.",
    read: (ctx) => {
      const a = ctx.trace?.analysis;
      if (!a) return notRun(ctx, "Did not run: there was no scout result to act on.");
      const g = a.grades;
      return {
        status: "ok",
        headline: `${int(a.analysed)} stocks analysed: ${int(g.A ?? 0)} grade A, ${int(g.B ?? 0)} grade B.`,
        facts: [{ label: "Analysed", value: int(a.analysed) }, { label: "Tradeable", value: a.tradeable === null ? "–" : int(a.tradeable) },
                ...Object.entries(a.setups).map(([k, v]) => ({ label: `Setup: ${k}`, value: int(v) }))],
      };
    },
  },
  {
    id: "selection",
    title: "Stock selection",
    machinePart: "Sorting gate with 1–3 slots per Sector",
    component: "Within each Signalled Sector, eligible stocks ranked by Setup Grade and risk; 1 to 3 Picks depending on confidence",
    lambda: "stock-selection",
    code: [{ file: "lse_stock_analysis/selection.py", symbol: "select_stocks, picks_allowed" }],
    description: "Only stocks in this week's Signalled Sectors are considered. The best-graded ones go through the gate; a stronger signal opens more slots (up to 3).",
    read: (ctx) => {
      const sel = ctx.trace?.selection;
      if (!sel) return notRun(ctx, "Did not run: there was no scout result to act on.");
      const picks = sel.flatMap((s) => s.picks);
      return {
        status: "ok",
        headline: `${picks.length} Pick${picks.length === 1 ? "" : "s"} from ${int(sel.reduce((s, x) => s + x.candidates, 0))} candidates in ${sel.length} Sector${sel.length === 1 ? "" : "s"}.`,
        facts: [],
        items: sel.map((s) => ({
          label: s.sector,
          value: `${s.candidates} candidates → ${s.picks.length ? s.picks.map((p) => p.ticker).join(", ") : "no Pick"}${s.picks_allowed ? ` (up to ${s.picks_allowed})` : ""}`,
          status: s.picks.length ? "ok" : s.candidates ? "warn" : "idle",
        })),
      };
    },
  },
  {
    id: "portfolio",
    title: "Paper Portfolio",
    machinePart: "Vault with balance scales",
    component: "The £1,000 Paper Portfolio: fixed rules decide sells and buys each week",
    lambda: "stock-selection",
    code: [{ file: "lse_stock_analysis/portfolio.py", symbol: "apply_week" },
           { file: "stock_selection_lambda/portfolio_step.py", symbol: "run_week" }],
    description: "The imaginary £1,000 portfolio. First, holdings that hit their stop, reached 6 weeks, or whose Sector stopped being signalled are sold. Then the Picks are bought in order of conviction until the cash runs out. The scales weigh cash against holdings.",
    read: (ctx) => {
      const h = ctx.history;
      if (!h) return idle(ctx.portfolio ? "The Paper Portfolio had not started yet." : "The Paper Portfolio has not started yet.");
      const buys = ctx.trades.filter((t) => t.side === "buy");
      const sells = ctx.trades.filter((t) => t.side === "sell");
      const skipped = ctx.notes.filter((n) => n.kind === "skipped");
      const headline = !h.scout_ok ? `No Trades: no scout result this week. Value ${gbp(h.total)}.`
        : `Sold ${sells.length}, bought ${buys.length}. Value ${gbp(h.total)} against a Benchmark of ${gbp(h.benchmark)}.`;
      return {
        status: !h.scout_ok ? "warn" : "ok",
        headline,
        facts: [{ label: "Cash", value: gbp(h.cash) }, { label: "Holdings", value: gbp(h.holdings) }, { label: "Total", value: gbp(h.total) },
                { label: "Benchmark", value: gbp(h.benchmark) }, ...(h.gbpusd ? [{ label: "GBP/USD", value: h.gbpusd.toFixed(4) }] : [])],
        items: [
          ...ctx.trades.map((t) => ({
            label: `${t.side === "buy" ? "Bought" : "Sold"} ${t.ticker}`,
            value: `${gbp(t.value_gbp)} · ${REASON_TEXT[t.reason]}${t.pnl_gbp !== null ? ` · P&L ${gbp(t.pnl_gbp, true)}` : ""}`,
            status: "ok" as StageStatus,
          })),
          ...skipped.map((n) => ({ label: `Skipped ${n.ticker}`, value: n.reason ?? n.message, status: "warn" as StageStatus })),
        ],
      };
    },
  },
  {
    id: "email",
    title: "Weekly email",
    machinePart: "Mail chute",
    component: "One email a week via Amazon SES: the sector report with the Picks inside each card and the portfolio's week",
    lambda: "stock-selection",
    code: [{ file: "stock_selection_lambda/email_report.py", symbol: "build_report_email" }],
    description: "Everything above goes out as one email: the scout's report, the Picks inside each Sector, and what the Paper Portfolio did.",
    read: (ctx) => {
      const sent = ctx.trace?.email.sent;
      if (sent === true) return { status: "ok", headline: "Sent.", facts: [] };
      if (sent === false) return { status: "fail", headline: "Not sent.", facts: [] };
      return notRun(ctx, "No email: there was no scout result to report.");
    },
  },
];

export const REASON_TEXT: Record<string, string> = {
  pick: "a Pick", stop: "hit its stop", max_hold: "held 6 weeks", sector_absent: "Sector no longer signalled",
};

export const STATUS_TEXT: Record<StageStatus, string> = {
  ok: "Ran", warn: "Ran with problems", fail: "Failed or did not run", idle: "No detail recorded",
};

export function readStages(ctx: WeekContext): { stage: Stage; reading: StageReading }[] {
  return STAGES.map((stage) => {
    try {
      return { stage, reading: stage.read(ctx) };
    } catch {
      return { stage, reading: idle() };       // a malformed trace never breaks the page
    }
  });
}
