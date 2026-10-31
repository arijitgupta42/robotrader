import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { loadSite, loadWeek, type Fetcher } from "../data/load";
import { readStages, stageContext } from "../data/stages";
import { machineState, scaleTilt, SECTORS } from "./machineState";

const FIXTURES = join(__dirname, "..", "..", "fixtures");
const fetcher: Fetcher = async (path) => {
  try {
    return JSON.parse(readFileSync(join(FIXTURES, path), "utf-8"));
  } catch {
    return null;
  }
};

async function stateFor(week: string) {
  const site = await loadSite(fetcher);
  const trace = await loadWeek(fetcher, week);
  const ctx = stageContext(week, trace, site.portfolio, site.ledger.trades, site.ledger.notes);
  return machineState(ctx, readStages(ctx));
}

describe("machine state", () => {
  it("drives every part from a full week", async () => {
    const m = await stateFor("2026-W44");
    expect(m.trigger).toBe("WeeklyTrigger");
    expect(m.feeds).toHaveLength(41);
    expect(m.feeds.every((f) => f.ok && f.weight > 0 && f.weight <= 1)).toBe(true);
    expect(Math.max(...m.feeds.map((f) => f.weight))).toBe(1);
    expect(m.reddit).toEqual({ planned: 7, ok: 7, throttled: 0 });
    expect(m.sieve!.kept + m.sieve!.removed).toBeCloseTo(1);
    expect(m.cylinders.map((c) => [c.name, c.ok, c.fallback])).toEqual([["news", true, false], ["reddit", true, false], ["consolidation", true, false]]);
    expect(m.spareFires).toBe(false);
    expect(m.dies).toHaveLength(31);
    expect(m.dies.filter((d) => d.lit).map((d) => d.sector)).toEqual(["AI Infrastructure", "Integrated Oil & Gas", "Electricity & Grid"]);
    expect(m.tube).toBe(true);
    expect(m.tanks).toEqual({ total: 16, flagged: 0 });
    expect(m.lanes.map((l) => [l.sector, l.slots, l.picks])).toEqual([
      ["Electricity & Grid", 2, ["SSE.L", "AEP"]], ["AI Infrastructure", 1, []], ["Integrated Oil & Gas", 1, ["ITH.L"]]]);
    expect(m.trades.map((t) => [t.side, t.ticker])).toEqual([["buy", "AEP"], ["buy", "SSE.L"], ["buy", "ITH.L"]]);
    expect(m.mail).toBe(true);
  });

  it("jams a failed feed, throttles Reddit and fires the spare cylinder when the fallback answered", async () => {
    const m = await stateFor("2026-W45");
    const bloomberg = m.feeds.find((f) => f.name === "Bloomberg Markets")!;
    expect(bloomberg).toEqual({ name: "Bloomberg Markets", ok: false, weight: 0 });
    expect(m.reddit).toEqual({ planned: 7, ok: 5, throttled: 2 });
    expect(m.spareFires).toBe(true);
    expect(m.cylinders.find((c) => c.name === "consolidation")!.fallback).toBe(true);
    expect(m.trades.some((t) => t.side === "sell" && t.ticker === "ITH.L")).toBe(true);
  });

  it("draws a week where the scout never succeeded as idle parts, with only the vault showing values", async () => {
    const m = await stateFor("2026-W46");
    expect(m.feeds).toEqual([]);
    expect(m.reddit).toBeNull();
    expect(m.sieve).toBeNull();
    expect(m.cylinders.every((c) => !c.ran)).toBe(true);
    expect(m.dies.some((d) => d.lit)).toBe(false);
    expect(m.tube).toBe(false);
    expect(m.tanks).toBeNull();
    expect(m.lanes).toEqual([]);
    expect(m.mail).toBeNull();
    expect(m.scale).not.toBeNull();
    expect(m.trades).toEqual([]);
    expect(m.status.feeds).toBe("fail");
  });

  it("leaves the scout's parts idle when its result has no trace block, but still stamps the signals", async () => {
    const m = await stateFor("2026-W47");
    expect(m.feeds).toEqual([]);
    expect(m.cylinders.every((c) => !c.ran)).toBe(true);
    expect(m.dies.filter((d) => d.lit)).toHaveLength(2);
    expect(m.status.feeds).toBe("idle");
  });

  it("keeps the Sector list in step with the scout's 31 Sectors", () => {
    expect(SECTORS).toHaveLength(31);
    expect(new Set(SECTORS).size).toBe(31);
  });

  it("tilts the scales towards whichever pan is heavier", () => {
    expect(scaleTilt({ cash: 1000, holdings: 0 })).toBeCloseTo(-0.35);
    expect(scaleTilt({ cash: 500, holdings: 500 })).toBe(0);
    expect(scaleTilt({ cash: 250, holdings: 750 })).toBeCloseTo(0.175);
    expect(scaleTilt(null)).toBe(0);
  });
});

describe("camera framing", () => {
  // Half the visible width at the target's distance must cover half the line (with the station plinths).
  const visibleHalfWidth = (distance: number, aspect: number, fov: number) => distance * Math.tan((fov * Math.PI) / 360) * aspect;

  it("fits the whole line on a wide screen without leaving it tiny", async () => {
    const { framing, HALF_WIDTH } = await import("./kit");
    for (const aspect of [1.6, 2.1, 2.6]) {
      const { distance } = framing(null, aspect, 35);
      const half = visibleHalfWidth(distance, aspect, 35);
      expect(half).toBeGreaterThanOrEqual(HALF_WIDTH);
      expect(half).toBeLessThan(HALF_WIDTH * 1.35);
    }
  });

  it("caps the overview on a phone and frames one station closely", async () => {
    const { framing, LAYOUT } = await import("./kit");
    expect(framing(null, 0.5, 35).distance).toBe(80);
    const engine = framing("llm", 0.5, 35);
    expect(engine.target).toEqual([LAYOUT.llm.x, 1.2, LAYOUT.llm.z]);
    expect(visibleHalfWidth(engine.distance, 0.5, 35)).toBeGreaterThan(LAYOUT.llm.w / 2);
    expect(engine.distance).toBeLessThan(40);
  });
});
