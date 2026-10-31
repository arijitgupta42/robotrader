// What each part of the 3D machine shows for one week, worked out from the stage readings and the
// week's data.  Pure functions only, so the mapping is tested without WebGL.  A part with no data is
// drawn idle: the machine never animates numbers it does not have.
import type { StageId, StageStatus, StageReading, Stage, WeekContext } from "../data/stages";

/** The scout's 31 Sectors (financial_market_news_analyzer/config.py, SECTORS), so each die keeps its place week to week. */
export const SECTORS = [
  "Semiconductors & EDA", "Cloud & SaaS", "Cybersecurity", "AI Infrastructure", "Data Centres & Digital Infrastructure",
  "UK Retail Banks", "Investment Banks & Brokers", "Insurance", "Asset Management & Wealth", "Fintech & Payments",
  "Integrated Oil & Gas", "Renewables & Clean Energy", "Oil Field Services", "Pharma & Biotech", "Medical Devices & Services",
  "UK General Retail", "Luxury & Lifestyle", "Travel, Leisure & Hospitality", "Grocery & Food Retail", "Consumer Staples & FMCG",
  "Aerospace & Defence", "Engineering & Industrials", "Logistics & Transport", "Diversified Mining",
  "Specialty Metals & Battery Materials", "Commercial & Logistics REITs", "Retail & Office REITs", "Housebuilders",
  "Electricity & Grid", "Water", "UK Telecoms & Broadband",
];

export const UNIVERSE_SIZE = 853;

export interface MachineState {
  status: Record<StageId, StageStatus>;
  trigger: string | null;
  feeds: { name: string; ok: boolean; weight: number }[];                 // weight: share of the busiest feed, 0..1
  reddit: { planned: number; ok: number; throttled: number } | null;
  sieve: { kept: number; removed: number } | null;                        // shares of what came in, 0..1
  cylinders: { name: "news" | "reddit" | "consolidation"; ran: boolean; ok: boolean; fallback: boolean; model: string | null }[];
  spareFires: boolean;
  dies: { sector: string; lit: boolean; confidence: number }[];
  tube: boolean;
  tanks: { total: number; flagged: number } | null;
  gauges: { A: number; B: number; C: number } | null;                    // shares of the analysed stocks, 0..1
  lanes: { sector: string; slots: number; picks: string[]; candidates: number }[];
  scale: { cash: number; holdings: number } | null;
  trades: { ticker: string; side: "buy" | "sell"; value: number }[];
  mail: boolean | null;
}

export function machineState(ctx: WeekContext, readings: { stage: Stage; reading: StageReading }[]): MachineState {
  const status = Object.fromEntries(readings.map(({ stage, reading }) => [stage.id, reading.status])) as Record<StageId, StageStatus>;
  const scout = ctx.trace?.scout.trace ?? null;

  const feedList = Array.isArray(scout?.feeds) ? scout!.feeds! : [];
  const busiest = Math.max(1, ...feedList.map((f) => f.fetched));
  const feeds = feedList.map((f) => ({ name: f.name, ok: f.ok, weight: f.ok ? Math.max(0.08, f.fetched / busiest) : 0 }));

  const r = scout?.reddit ?? null;
  const reddit = r ? {
    planned: r.planned ?? r.requests?.length ?? 0,
    ok: r.succeeded ?? 0,
    throttled: (r.requests ?? []).filter((q) => q.status === "throttled").length,
  } : null;

  const h = scout?.headlines ?? null;
  const sieve = h && h.fetched > 0 ? { kept: h.kept / h.fetched, removed: 1 - h.kept / h.fetched } : null;

  const llm = scout?.llm ?? {};
  const cylinders = (["news", "reddit", "consolidation"] as const).map((name) => {
    const p = llm[name];
    return { name, ran: Boolean(p), ok: Boolean(p?.ok), fallback: Boolean(p?.fallback), model: p?.model_used ?? null };
  });

  const signals = ctx.trace?.scout.signals ?? [];
  const bySector = new Map(signals.map((s) => [s.sector, s.confidence]));
  const names = [...SECTORS, ...signals.map((s) => s.sector).filter((s) => !SECTORS.includes(s))];
  const dies = names.map((sector) => ({ sector, lit: bySector.has(sector), confidence: bySector.get(sector) ?? 0 }));

  const p = ctx.trace?.prices ?? null;
  const tanks = p ? { total: p.requested, flagged: p.no_data + p.stale + p.anomalies + p.short_history } : null;

  const a = ctx.trace?.analysis ?? null;
  const analysed = a ? Math.max(1, a.analysed) : 1;
  const gauges = a ? { A: (a.grades.A ?? 0) / analysed, B: (a.grades.B ?? 0) / analysed, C: (a.grades.C ?? 0) / analysed } : null;

  const lanes = (ctx.trace?.selection ?? []).map((s) => ({
    sector: s.sector, slots: s.picks_allowed ?? Math.max(1, s.picks.length), picks: s.picks.map((x) => x.ticker), candidates: s.candidates,
  }));

  const hist = ctx.history;
  return {
    status,
    trigger: scout?.trigger ?? null,
    feeds, reddit, sieve, cylinders,
    spareFires: cylinders.some((c) => c.fallback),
    dies,
    tube: ctx.trace !== null,
    tanks, gauges, lanes,
    scale: hist ? { cash: hist.cash, holdings: hist.holdings } : null,
    trades: ctx.trades.map((t) => ({ ticker: t.ticker, side: t.side, value: t.value_gbp })),
    mail: ctx.trace?.email.sent ?? null,
  };
}

/** Beam tilt of the vault's scales in radians: positive when holdings outweigh cash, at most ±0.35. */
export function scaleTilt(scale: { cash: number; holdings: number } | null): number {
  if (!scale) return 0;
  const total = scale.cash + scale.holdings;
  if (total <= 0) return 0;
  return Math.max(-0.35, Math.min(0.35, ((scale.holdings - scale.cash) / total) * 0.35));
}
