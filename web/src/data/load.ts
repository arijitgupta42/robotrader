import type { LedgerData, PortfolioData, SiteData, WeekIndex, WeekTrace } from "./types";

/** Fetches JSON relative to the site's data folder; null for a file that is not there (404). */
export type Fetcher = (path: string) => Promise<unknown | null>;

export function httpFetcher(base: string): Fetcher {
  return async (path) => {
    const res = await fetch(`${base}data/${path}`, { cache: "no-cache" });
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`Could not load ${path}: HTTP ${res.status}`);
    return res.json();
  };
}

/** Every week the site knows about: portfolio history plus weeks with a Run Trace, oldest first. */
export function allWeeks(portfolio: PortfolioData | null, traceWeeks: string[]): string[] {
  const set = new Set<string>(traceWeeks);
  for (const h of portfolio?.history ?? []) set.add(h.week);
  return [...set].sort();          // ISO week labels sort in time order
}

export async function loadSite(fetcher: Fetcher): Promise<SiteData> {
  const [portfolio, ledger, index] = await Promise.all([
    fetcher("portfolio.json") as Promise<PortfolioData | null>,
    fetcher("ledger.json") as Promise<LedgerData | null>,
    fetcher("weeks/index.json") as Promise<WeekIndex | null>,
  ]);
  const traceWeeks = index?.weeks ?? [];
  return {
    portfolio,
    ledger: ledger ?? { trades: [], notes: [] },
    traceWeeks,
    weeks: allWeeks(portfolio, traceWeeks),
  };
}

/** One week's Run Trace, or null when that week has none (for example the scout never succeeded). */
export async function loadWeek(fetcher: Fetcher, week: string): Promise<WeekTrace | null> {
  if (!/^\d{4}-W\d{2}$/.test(week)) throw new Error(`Not a week label: ${week}`);
  return (await fetcher(`weeks/${week}.json`)) as WeekTrace | null;
}

/** "2026-W44" -> "Week 44, 2026 (26 Oct)" using the ISO week's Monday. */
export function weekTitle(week: string): string {
  const m = /^(\d{4})-W(\d{2})$/.exec(week);
  if (!m) return week;
  const year = Number(m[1]);
  const n = Number(m[2]);
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7) + (n - 1) * 7);
  const day = monday.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  return `Week ${n}, ${year} (w/c ${day})`;
}
