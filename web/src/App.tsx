import { useEffect, useMemo, useState } from "react";

import { httpFetcher, loadSite, loadWeek, weekTitle } from "./data/load";
import { readStages, stageContext } from "./data/stages";
import type { SiteData, WeekTrace } from "./data/types";
import { PlainView } from "./components/PlainView";
import { Holdings, HowItWorks, Ledger, PortfolioSummary } from "./components/Portfolio";
import { ValueChart } from "./components/ValueChart";
import { WeekPicker } from "./components/WeekPicker";

const fetcher = httpFetcher(import.meta.env.BASE_URL);

function weekFromHash(): string | null {
  const m = /^#(\d{4}-W\d{2})$/.exec(window.location.hash);
  return m ? m[1] : null;
}

export default function App() {
  const [site, setSite] = useState<SiteData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [week, setWeek] = useState<string | null>(null);
  const [trace, setTrace] = useState<WeekTrace | null>(null);
  const [traceFor, setTraceFor] = useState<string | null>(null);
  const [openStage, setOpenStage] = useState<string | null>(null);

  useEffect(() => {
    loadSite(fetcher).then((s) => {
      setSite(s);
      const fromHash = weekFromHash();
      setWeek(fromHash && s.weeks.includes(fromHash) ? fromHash : s.weeks[s.weeks.length - 1] ?? null);
    }).catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!week || !site) return;
    if (window.location.hash !== `#${week}`) history.replaceState(null, "", `#${week}`);
    let live = true;
    const load = site.traceWeeks.includes(week) ? loadWeek(fetcher, week) : Promise.resolve(null);
    load.then((t) => { if (live) { setTrace(t); setTraceFor(week); } })
        .catch(() => { if (live) { setTrace(null); setTraceFor(week); } });
    return () => { live = false; };
  }, [week, site]);

  const readings = useMemo(() => {
    if (!site || !week || traceFor !== week) return null;
    return readStages(stageContext(week, trace, site.portfolio, site.ledger.trades, site.ledger.notes));
  }, [site, week, trace, traceFor]);

  if (error) return <main className="page"><div className="card empty">Could not load the data: {error}</div></main>;
  if (!site) return <main className="page"><div className="empty" aria-busy="true">Loading…</div></main>;

  const p = site.portfolio;
  return (
    <main className="page">
      <header className="masthead">
        <div>
          <h1>Robotrader</h1>
          <p>A weekly sector scout, the stocks it picks, and the imaginary £1,000 it trades with them.</p>
        </div>
        {p && <div className="stamp">Updated {new Date(p.updated_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC · updates weekly</div>}
      </header>

      {!p ? (
        <div className="card empty">The Paper Portfolio starts with the first weekly run. Check back after Saturday's run.</div>
      ) : (
        <>
          <PortfolioSummary p={p} />
          <section className="section" aria-labelledby="perf">
            <h2 id="perf" className="sr-only">Performance</h2>
            <div className="grid-2">
              <ValueChart history={p.history} startingCash={p.starting_cash} selectedWeek={week} onSelectWeek={setWeek} />
              <HowItWorks p={p} />
            </div>
          </section>
        </>
      )}

      {week && (
        <section className="section" aria-labelledby="run">
          <h2 id="run">The weekly run</h2>
          <p className="lede">What each part of the pipeline did in {weekTitle(week)}, in the order the code runs it. Open a stage to see the code behind it and that week's numbers.</p>
          <div className="toolbar">
            <WeekPicker weeks={site.weeks} value={week} onChange={setWeek} />
          </div>
          {readings ? <PlainView readings={readings} openStage={openStage} onOpen={setOpenStage} />
            : <div className="empty" aria-busy="true">Loading {week}…</div>}
        </section>
      )}

      {p && (
        <>
          <section className="section" aria-labelledby="holdings">
            <h2 id="holdings">Holdings</h2>
            <p className="lede">What the Paper Portfolio owns after the latest run ({p.week}). Prices are in each stock's own currency; values are in pounds.</p>
            <div className="card"><Holdings p={p} /></div>
          </section>
          <section className="section" aria-labelledby="ledger">
            <h2 id="ledger">Ledger</h2>
            <p className="lede">Every Trade, newest first. Each buy shows the signal behind it. The selected week is highlighted.</p>
            <div className="card"><Ledger ledger={site.ledger} selectedWeek={week} onSelectWeek={setWeek} /></div>
          </section>
        </>
      )}

      <footer className="footer">
        Imaginary money. Sector signals come from an LLM reading public news and Reddit; picks and trades are rule-based.
        Not investment advice. <a href="https://github.com/arijitgupta42/robotrader">Source code</a>.
      </footer>
    </main>
  );
}
