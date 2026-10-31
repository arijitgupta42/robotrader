import { gbp, pct, price } from "../data/format";
import { REASON_TEXT } from "../data/stages";
import type { LedgerData, PortfolioData } from "../data/types";

export function PortfolioSummary({ p }: { p: PortfolioData }) {
  const change = p.total / p.starting_cash - 1;
  const bench = p.benchmark / p.starting_cash - 1;
  const ahead = p.total - p.benchmark;
  return (
    <div className="hero">
      <div className="card hero-value">
        <div className="label">Paper Portfolio value · {p.week}</div>
        <div className="figure">{gbp(p.total)}</div>
        <div>
          <span className={`delta ${change >= 0 ? "up" : "down"}`}>{change >= 0 ? "▲" : "▼"} {pct(change, true, 2)}</span>
          <span className="muted"> since {p.opening_week} ({gbp(p.starting_cash)})</span>
        </div>
        <div className="small" style={{ marginTop: 6 }}>
          {Math.abs(ahead) < 0.005 ? "Level with" : ahead > 0 ? `${gbp(ahead)} ahead of` : `${gbp(-ahead)} behind`} the Benchmark ({pct(bench, true, 2)})
        </div>
      </div>
      <div className="tiles">
        <div className="tile"><div className="label">Cash</div><div className="value">{gbp(p.cash)}</div>
          <div className="sub">{pct(p.cash / p.total)} of the portfolio</div></div>
        <div className="tile"><div className="label">Holdings</div><div className="value">{gbp(p.holdings)}</div>
          <div className="sub">{p.positions.length} of {p.rules.max_positions} positions</div></div>
        <div className="tile"><div className="label">Benchmark</div><div className="value">{gbp(p.benchmark)}</div>
          <div className="sub">equal-weight Universe</div></div>
        <div className="tile"><div className="label">Extra money added</div><div className="value">£0.00</div>
          <div className="sub">every trade comes from the £1,000</div></div>
      </div>
    </div>
  );
}

export function Holdings({ p }: { p: PortfolioData }) {
  if (!p.positions.length) return <p className="muted">No positions: everything is in cash.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Stock</th><th>Sector</th><th className="num">Bought</th><th className="num">Now</th><th className="num">Stop</th>
            <th className="num">Value</th><th className="num">P&amp;L</th><th className="num">Held</th></tr>
        </thead>
        <tbody>
          {p.positions.map((x) => (
            <tr key={x.ticker}>
              <td><span className="ticker">{x.ticker}</span> <span className="tag">{x.market}</span><div className="small muted">{x.company}</div></td>
              <td className="small">{x.sector}{x.sector_absent_weeks > 0 && <div className="muted">not signalled {x.sector_absent_weeks} wk</div>}</td>
              <td className="num">{price(x.entry_price, x.market)}<div className="small muted">{x.entry_week}</div></td>
              <td className="num">{price(x.last_price, x.market)}
                {(x.stale || x.fx_stale) && <div className="small muted">{x.stale ? "stale price" : "old FX rate"}</div>}</td>
              <td className="num">{price(x.stop_price, x.market)}</td>
              <td className="num">{gbp(x.value_gbp)}</td>
              <td className={`num delta ${x.pnl_gbp >= 0 ? "up" : "down"}`}>{gbp(x.pnl_gbp, true)}</td>
              <td className="num">{x.weeks_held} wk<div className="small muted">{x.weeks_to_max_hold} to go</div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Ledger({ ledger, selectedWeek, onSelectWeek }: {
  ledger: LedgerData; selectedWeek: string | null; onSelectWeek: (w: string) => void;
}) {
  const trades = [...ledger.trades].reverse();
  const weekNotes = ledger.notes.filter((n) => n.week === selectedWeek && n.kind !== "skipped");
  if (!trades.length) return <p className="muted">No Trades yet.</p>;
  return (
    <>
      {weekNotes.map((n, i) => <p key={i} className="notice">{n.week}: {n.message}</p>)}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Week</th><th>Trade</th><th>Why</th><th className="num">Value</th><th className="num">Cost</th><th className="num">P&amp;L</th></tr></thead>
          <tbody>
            {trades.map((t, i) => (
              <tr key={`${t.week}-${t.ticker}-${t.side}-${i}`} className={t.week === selectedWeek ? "highlight" : undefined}>
                <td><button type="button" className="text-btn" onClick={() => onSelectWeek(t.week)} aria-label={`Show the run for ${t.week}`}>{t.week.slice(5)}</button></td>
                <td><span className="tag">{t.side === "buy" ? "BUY" : "SELL"}</span> <span className="ticker">{t.ticker}</span>
                  <div className="small muted">{t.company}</div></td>
                <td className="small">
                  {t.side === "buy" && t.signal ? <>Pick in <b>{t.signal.sector}</b> ({pct(t.signal.confidence)}, {t.signal.convergence_type})</> : REASON_TEXT[t.reason]}
                </td>
                <td className="num">{gbp(t.value_gbp)}</td>
                <td className="num">{gbp(t.cost_gbp)}</td>
                <td className={`num ${t.pnl_gbp === null ? "" : `delta ${t.pnl_gbp >= 0 ? "up" : "down"}`}`}>{t.pnl_gbp === null ? "" : gbp(t.pnl_gbp, true)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function HowItWorks({ p }: { p: PortfolioData }) {
  const r = p.rules;
  return (
    <div className="card small">
      <h3 style={{ marginTop: 0, fontSize: 15 }}>How the Paper Portfolio trades</h3>
      <ul style={{ paddingLeft: 18, margin: 0, display: "grid", gap: 4 }}>
        <li>It started with {gbp(p.starting_cash)} in {p.opening_week}. No money is ever added; every buy is paid for from cash.</li>
        <li>Once a week, after the scout runs, it first <b>sells</b> a holding that hit its stop, has been held {r.max_hold_weeks} weeks, or whose Sector has been missing from the signals {r.sector_absent_weeks} weeks running.</li>
        <li>Then it <b>buys</b> this week's Picks, strongest signal first: each sized from the risk analysis times the signal's confidence, at most {pct(r.max_position_weight)} of the portfolio, at most {r.max_positions} holdings, {pct(r.cash_floor)} always kept as cash, nothing under {gbp(r.min_trade_gbp)}.</li>
        <li>Costs: {pct(r.stamp_duty, false, 1)} stamp duty on UK buys, {pct(r.fx_fee, false, 1)} currency fee on US trades. US stocks are valued at that week's GBP/USD rate.</li>
        <li>The <b>Benchmark</b> is {gbp(p.starting_cash)} earning the average weekly return of every stock the system can pick from.</li>
      </ul>
      <p className="muted" style={{ marginBottom: 0 }}>
        Limits worth knowing: trades are filled at the Friday close the analysis used, which a real order placed after the
        Saturday run could not get; stops are only checked once a week, so a holding can fall well past its stop between checks.
        Rules version {p.portfolio_version}. Imaginary money, not investment advice.
      </p>
    </div>
  );
}
