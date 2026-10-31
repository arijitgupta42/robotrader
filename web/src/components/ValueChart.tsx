import { useEffect, useRef, useState } from "react";

import { gbp } from "../data/format";
import { weekTitle } from "../data/load";
import type { HistoryPoint } from "../data/types";

const HEIGHT = 240;
const PAD = { top: 16, right: 92, bottom: 28, left: 56 };

function niceTicks(min: number, max: number, count = 4): number[] {
  const span = max - min || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const ticks: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(Number(v.toFixed(6)));
  return ticks;
}

/** The Paper Portfolio's value against its Benchmark, week by week. */
export function ValueChart({ history, startingCash, selectedWeek, onSelectWeek }: {
  history: HistoryPoint[];
  startingCash: number;
  selectedWeek: string | null;
  onSelectWeek: (week: string) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const n = history.length;
  const values = history.flatMap((h) => [h.total, h.benchmark]).concat(startingCash);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const padY = Math.max((hi - lo) * 0.15, 5);
  const ticks = niceTicks(lo - padY, hi + padY);
  const yMin = Math.min(ticks[0], lo - padY);
  const yMax = Math.max(ticks[ticks.length - 1], hi + padY);
  const narrow = width < 480;
  const right = narrow ? 16 : PAD.right;
  const plotW = width - PAD.left - right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (v: number) => PAD.top + (1 - (v - yMin) / (yMax - yMin)) * plotH;
  const path = (key: "total" | "benchmark") => history.map((h, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(h[key]).toFixed(1)}`).join("");
  const labelEvery = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(plotW / 64))));
  const active = hover ?? (selectedWeek ? history.findIndex((h) => h.week === selectedWeek) : -1);
  const last = history[n - 1];

  function indexAt(clientX: number): number {
    const rect = wrap.current!.getBoundingClientRect();
    const px = clientX - rect.left - PAD.left;
    return n <= 1 ? 0 : Math.max(0, Math.min(n - 1, Math.round((px / plotW) * (n - 1))));
  }

  function onKey(e: React.KeyboardEvent) {
    const cur = active >= 0 ? active : n - 1;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const next = Math.max(0, Math.min(n - 1, cur + (e.key === "ArrowLeft" ? -1 : 1)));
      setHover(next);
      onSelectWeek(history[next].week);
    }
  }

  const tipLeft = active >= 0 ? Math.min(Math.max(x(active) - 90, 0), width - 190) : 0;

  return (
    <div className="card">
      <div className="chart-head">
        <h3 id="value-chart-title">Value each week</h3>
        <div className="legend" aria-hidden="true">
          <span><span className="key" style={{ background: "var(--series-1)" }} />Paper Portfolio</span>
          <span><span className="key" style={{ background: "var(--series-2)" }} />Benchmark</span>
          <span><span className="key" style={{ background: "var(--axis)", height: 0, borderTop: "1.5px dashed var(--ink-muted)" }} />Starting £1,000</span>
        </div>
        <button type="button" className="text-btn" onClick={() => setShowTable((s) => !s)} aria-expanded={showTable}>
          {showTable ? "Show chart" : "Show as table"}
        </button>
      </div>
      {showTable ? (
        <div className="table-wrap">
          <table aria-labelledby="value-chart-title">
            <thead><tr><th>Week</th><th className="num">Paper Portfolio</th><th className="num">Benchmark</th><th className="num">Cash</th><th className="num">Trades</th></tr></thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.week} className={h.week === selectedWeek ? "highlight" : undefined}>
                  <td>{h.week}{!h.scout_ok && <span className="muted small"> (no scout result)</span>}</td>
                  <td className="num">{gbp(h.total)}</td><td className="num">{gbp(h.benchmark)}</td>
                  <td className="num">{gbp(h.cash)}</td><td className="num">{h.trades}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="chart" ref={wrap}>
          <svg height={HEIGHT} role="img" aria-labelledby="value-chart-title"
               aria-describedby="value-chart-desc">
            <desc id="value-chart-desc">
              {last ? `Latest week ${last.week}: Paper Portfolio ${gbp(last.total)}, Benchmark ${gbp(last.benchmark)}.` : ""}
              {" "}Use the left and right arrow keys to step through the weeks.
            </desc>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={PAD.left} x2={width - right} y1={y(t)} y2={y(t)} stroke="var(--grid)" strokeWidth={1} />
                <text x={PAD.left - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--ink-muted)"
                      style={{ fontVariantNumeric: "tabular-nums" }}>£{t.toLocaleString("en-GB")}</text>
              </g>
            ))}
            <line x1={PAD.left} x2={width - right} y1={y(startingCash)} y2={y(startingCash)}
                  stroke="var(--ink-muted)" strokeDasharray="4 4" strokeWidth={1.5} />
            <line x1={PAD.left} x2={width - right} y1={PAD.top + plotH} y2={PAD.top + plotH} stroke="var(--axis)" />
            {history.map((h, i) => (i % labelEvery === 0 || i === n - 1) && (
              <text key={h.week} x={x(i)} y={HEIGHT - 8} textAnchor="middle" fontSize={11} fill="var(--ink-muted)">{h.week.slice(5)}</text>
            ))}
            {history.map((h, i) => !h.scout_ok && (
              <text key={`nt-${h.week}`} x={x(i)} y={PAD.top + plotH - 6} textAnchor="middle" fontSize={10} fill="var(--ink-muted)">no run</text>
            ))}
            <path d={path("benchmark")} fill="none" stroke="var(--series-2)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            <path d={path("total")} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {n <= 2 && history.map((h, i) => (
              <g key={`pt-${h.week}`}>
                <circle cx={x(i)} cy={y(h.benchmark)} r={4} fill="var(--series-2)" stroke="var(--surface)" strokeWidth={2} />
                <circle cx={x(i)} cy={y(h.total)} r={4} fill="var(--series-1)" stroke="var(--surface)" strokeWidth={2} />
              </g>
            ))}
            {last && !narrow && (() => {
              const yt = y(last.total);
              let yb = y(last.benchmark);
              if (Math.abs(yt - yb) < 14) yb = yt + (yb >= yt ? 14 : -14);
              return (
                <g fontSize={12}>
                  <text x={x(n - 1) + 10} y={yt} dy="0.32em" fill="var(--ink)" fontWeight={600}>{gbp(last.total)}</text>
                  <text x={x(n - 1) + 10} y={yb} dy="0.32em" fill="var(--ink-2)">{gbp(last.benchmark)}</text>
                </g>
              );
            })()}
            {active >= 0 && (
              <g pointerEvents="none">
                <line x1={x(active)} x2={x(active)} y1={PAD.top} y2={PAD.top + plotH} stroke="var(--ink-muted)" strokeWidth={1} />
                <circle cx={x(active)} cy={y(history[active].benchmark)} r={5} fill="var(--series-2)" stroke="var(--surface)" strokeWidth={2} />
                <circle cx={x(active)} cy={y(history[active].total)} r={5} fill="var(--series-1)" stroke="var(--surface)" strokeWidth={2} />
              </g>
            )}
            <rect className="hit" x={PAD.left - 12} y={PAD.top} width={plotW + 24} height={plotH} fill="transparent" tabIndex={0}
                  aria-label="Step through the weeks with the arrow keys; click to show that week's run"
                  onPointerMove={(e) => setHover(indexAt(e.clientX))} onPointerLeave={() => setHover(null)}
                  onClick={(e) => onSelectWeek(history[indexAt(e.clientX)].week)} onKeyDown={onKey}
                  onBlur={() => setHover(null)} />
          </svg>
          {hover !== null && history[hover] && (
            <div className="tooltip" style={{ left: tipLeft, top: 4 }} role="status">
              <div className="t">{weekTitle(history[hover].week)}</div>
              <div className="row"><span><span className="key" style={{ display: "inline-block", width: 12, height: 2, background: "var(--series-1)" }} />Paper Portfolio</span><strong>{gbp(history[hover].total)}</strong></div>
              <div className="row"><span><span className="key" style={{ display: "inline-block", width: 12, height: 2, background: "var(--series-2)" }} />Benchmark</span><strong>{gbp(history[hover].benchmark)}</strong></div>
              <div className="row"><span>Trades</span><strong>{history[hover].trades}</strong></div>
              {!history[hover].scout_ok && <div className="muted small">No scout result: no Trades this week.</div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
