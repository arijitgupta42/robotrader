// The website's data, as published by stock_selection_lambda/portfolio_step.py.
// docs/site-data.md is the contract: change both sides together.

export type Market = "LSE" | "US";

export interface SignalRef {
  sector: string;
  confidence: number;
  convergence_type: string;
}

export interface Position {
  ticker: string;
  company: string;
  market: Market;
  currency: string;
  sector: string | null;
  quantity: number;
  entry_week: string;
  entry_price: number;
  entry_fx: number | null;
  cost_basis_gbp: number;
  stop_price: number;
  last_price: number;
  last_fx: number | null;
  value_gbp: number;
  pnl_gbp: number;
  weeks_held: number;
  weeks_to_max_hold: number;
  sector_absent_weeks: number;
  stale: boolean;
  fx_stale: boolean;
  signal: SignalRef | Record<string, never>;
}

export interface HistoryPoint {
  week: string;
  cash: number;
  holdings: number;
  total: number;
  benchmark: number;
  trades: number;
  scout_ok: boolean;
  gbpusd: number | null;
}

export interface Rules {
  max_position_weight: number;
  cash_floor: number;
  max_positions: number;
  min_trade_gbp: number;
  stamp_duty: number;
  fx_fee: number;
  max_hold_weeks: number;
  sector_absent_weeks: number;
}

export interface PortfolioData {
  updated_at: string;
  week: string;
  portfolio_version: string;
  starting_cash: number;
  opening_week: string;
  cash: number;
  holdings: number;
  total: number;
  benchmark: number;
  positions: Position[];
  history: HistoryPoint[];
  rules: Rules;
}

export type TradeReason = "pick" | "stop" | "max_hold" | "sector_absent";

export interface Trade {
  week: string;
  side: "buy" | "sell";
  ticker: string;
  company: string;
  market: Market;
  currency: string;
  sector: string | null;
  reason: TradeReason;
  quantity: number;
  price: number;
  fx: number | null;
  value_gbp: number;
  cost_gbp: number;
  pnl_gbp: number | null;
  signal: SignalRef | null;
}

export type NoteKind = "skipped" | "stale_price" | "missing_fx" | "no_trade_week";

export interface Note {
  week: string;
  kind: NoteKind;
  ticker: string | null;
  reason?: string;
  message: string;
}

export interface LedgerData {
  trades: Trade[];
  notes: Note[];
}

export interface WeekIndex {
  updated_at: string;
  weeks: string[];
}

// --- Run Trace -------------------------------------------------------------

export interface FeedTrace {
  name: string;
  fetched: number;
  ok: boolean;
  error: string | null;
  fresh: number | null;
  kept: number | null;
}

export interface LlmPassTrace {
  models: string[];
  model_used: string | null;
  ok: boolean;
  seconds: number;
  fallback: boolean;
}

export interface RedditRequestTrace {
  label: string;
  status: "ok" | "empty" | "throttled" | "failed";
  posts: number;
  retried: boolean;
}

export interface ScoutTrace {
  trigger?: string;
  feeds?: FeedTrace[];
  headlines?: { fetched: number; duplicates: number; stale: number; fresh: number; cap: number; kept: number; max_age_days: number } | null;
  reddit?: {
    planned?: number; succeeded?: number; unique_posts?: number; fresh?: number; kept?: number; cap?: number;
    subreddits?: number; seconds?: number; requests?: RedditRequestTrace[];
  } | null;
  llm?: Partial<Record<"news" | "reddit" | "consolidation", LlmPassTrace>>;
  signals?: Record<string, number | boolean>;
  timings?: Record<string, number>;
}

export interface PublicSignal {
  sector: string;
  confidence: number;
  convergence_type: string;
  bear_case_probability?: number | null;
  disruption_type?: string | null;
  disruption_strength?: number | null;
  time_to_impact_weeks?: number | null;
  source_diversity?: number | null;
  rationale?: string | null;
  propagation?: string | null;
  invalidation_risk?: string | null;
  convergence_note?: string | null;
  key_catalysts?: string[] | null;
  correlated_sectors?: string[] | null;
}

export interface SelectionTrace {
  sector: string;
  confidence: number | null;
  convergence_type: string | null;
  candidates: number;
  eligible_checked: number;
  picks_allowed: number | null;
  picks: { ticker: string; company: string; market: Market | null; setup_grade: string | null; risk_score: number | null; close: number | null }[];
  runners_up: { ticker: string; company: string; reason: string | null }[];
}

export interface WeekTrace {
  week: string;
  generated_at: string;
  methodology_version: string;
  portfolio_version: string;
  scout: {
    timestamp: string | null;
    prompt_version: string | null;
    models: string[] | null;
    macro: string | null;
    signals: PublicSignal[];
    trace: ScoutTrace | null;
  };
  prices: {
    requested: number; returned: number; no_data: number; short_history: number; stale: number; anomalies: number;
    unit_fixed: number; cutoffs: Record<string, string>; coverage?: number;
  } | null;
  analysis: { analysed: number; failed: number; grades: Record<string, number>; setups: Record<string, number>; tradeable: number | null } | null;
  selection: SelectionTrace[] | null;
  email: { sent: boolean | null };
}

/** Everything the site shows, for every week. */
export interface SiteData {
  portfolio: PortfolioData | null;   // null before the Paper Portfolio's first week
  ledger: LedgerData;
  traceWeeks: string[];              // weeks that have a Run Trace
  weeks: string[];                   // every week the site knows about, oldest first
}
