import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { gbp, pct, price } from "./format";
import { allWeeks, loadSite, loadWeek, weekTitle, type Fetcher } from "./load";
import { readStages, stageContext, STAGES, type StageId } from "./stages";
import type { WeekTrace } from "./types";

const FIXTURES = join(__dirname, "..", "..", "fixtures");

const fixtureFetcher: Fetcher = async (path) => {
  try {
    return JSON.parse(readFileSync(join(FIXTURES, path), "utf-8"));
  } catch {
    return null;
  }
};

async function contextFor(week: string) {
  const site = await loadSite(fixtureFetcher);
  const trace = await loadWeek(fixtureFetcher, week);
  const ctx = stageContext(week, trace, site.portfolio, site.ledger.trades, site.ledger.notes);
  return Object.fromEntries(readStages(ctx).map(({ stage, reading }) => [stage.id, reading])) as Record<StageId, ReturnType<typeof readStages>[number]["reading"]>;
}

describe("loading", () => {
  it("loads every week the site knows about, including weeks without a Run Trace", async () => {
    const site = await loadSite(fixtureFetcher);
    expect(site.traceWeeks).toEqual(["2026-W44", "2026-W45", "2026-W47"]);
    expect(site.weeks).toEqual(["2026-W44", "2026-W45", "2026-W46", "2026-W47"]);
    expect(site.portfolio?.history.map((h) => h.week)).toEqual(site.weeks);
    expect(site.ledger.trades.length).toBeGreaterThan(0);
  });

  it("treats a site with no data yet as empty, not an error", async () => {
    const site = await loadSite(async () => null);
    expect(site).toEqual({ portfolio: null, ledger: { trades: [], notes: [] }, traceWeeks: [], weeks: [] });
  });

  it("returns null for a week without a Run Trace and refuses bad week labels", async () => {
    expect(await loadWeek(fixtureFetcher, "2026-W46")).toBeNull();
    await expect(loadWeek(fixtureFetcher, "../portfolio")).rejects.toThrow("Not a week label");
  });

  it("merges and sorts weeks", () => {
    expect(allWeeks(null, ["2027-W01", "2026-W52"])).toEqual(["2026-W52", "2027-W01"]);
  });
});

describe("the stage model", () => {
  it("has one entry per pipeline stage, in pipeline order, each with code to point at", () => {
    expect(STAGES.map((s) => s.id)).toEqual(
      ["schedule", "feeds", "reddit", "sieve", "llm", "signals", "handoff", "prices", "analysis", "selection", "portfolio", "email"]);
    for (const s of STAGES) {
      expect(s.code.length).toBeGreaterThan(0);
      expect(s.description.length).toBeGreaterThan(20);
      expect(s.machinePart).not.toEqual("");
    }
  });

  it("reads a full week", async () => {
    const r = await contextFor("2026-W44");
    expect(Object.values(r).every((x) => x.status !== "idle")).toBe(true);
    expect(r.feeds.status).toBe("ok");
    expect(r.feeds.headline).toMatch(/^41 of 41 sources answered/);
    expect(r.signals.headline).toContain("Electricity & Grid 71%");
    expect(r.selection.headline).toBe("3 Picks from 9 candidates in 3 Sectors.");
    expect(r.portfolio.headline).toMatch(/^Sold 0, bought 3\. Value £998\.48/);
    expect(r.email.status).toBe("ok");
  });

  it("flags the problems in a week where things went wrong", async () => {
    const r = await contextFor("2026-W45");
    expect(r.feeds.status).toBe("warn");
    expect(r.feeds.facts.find((f) => f.label === "Failed")?.value).toContain("Bloomberg Markets");
    expect(r.reddit.status).toBe("warn");
    expect(r.llm.status).toBe("warn");
    expect(r.llm.headline).toBe("The fallback model answered the consolidation pass.");
    expect(r.portfolio.items?.some((i) => i.label === "Sold ITH.L" && i.value.includes("hit its stop"))).toBe(true);
  });

  it("shows a week where the scout never succeeded as not run, never as invented numbers", async () => {
    const r = await contextFor("2026-W46");
    expect(r.schedule.status).toBe("fail");
    for (const id of ["feeds", "reddit", "sieve", "llm", "signals", "prices", "selection", "email"] as StageId[]) {
      expect(r[id].status).toBe("fail");
      expect(r[id].facts).toEqual([]);
    }
    expect(r.portfolio.status).toBe("warn");
    expect(r.portfolio.headline).toMatch(/^No Trades: no scout result this week/);
  });

  it("shows the scout's stages as 'no detail recorded' when its result has no trace block", async () => {
    const r = await contextFor("2026-W47");
    for (const id of ["schedule", "feeds", "reddit", "sieve", "llm"] as StageId[]) expect(r[id].status).toBe("idle");
    expect(r.signals.status).toBe("ok");
    expect(r.selection.status).toBe("ok");
    expect(r.portfolio.status).toBe("ok");
  });

  it("never throws on a malformed trace", () => {
    const broken = { week: "2026-W44", scout: { signals: null, trace: { feeds: "nope" } } } as unknown as WeekTrace;
    const readings = readStages(stageContext("2026-W44", broken, null, [], []));
    expect(readings).toHaveLength(STAGES.length);
  });
});

describe("formatting", () => {
  it("formats money, percentages, prices and weeks", () => {
    expect(gbp(1234.5)).toBe("£1,234.50");
    expect(gbp(-3.25, true)).toBe("−£3.25");
    expect(gbp(0.4, true)).toBe("+£0.40");
    expect(pct(0.712)).toBe("71%");
    expect(pct(-0.0123, true, 1)).toBe("−1.2%");
    expect(price(2627, "LSE")).toBe("2,627.00p");
    expect(price(130.21, "US")).toBe("$130.21");
    expect(weekTitle("2026-W44")).toBe("Week 44, 2026 (w/c 26 Oct)");
    expect(weekTitle("2027-W01")).toBe("Week 1, 2027 (w/c 4 Jan)");
  });
});
