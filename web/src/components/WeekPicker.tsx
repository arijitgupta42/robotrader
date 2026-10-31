import { weekTitle } from "../data/load";

export function WeekPicker({ weeks, value, onChange }: { weeks: string[]; value: string; onChange: (w: string) => void }) {
  const i = weeks.indexOf(value);
  return (
    <div className="week-picker">
      <button type="button" className="icon-btn" onClick={() => onChange(weeks[i - 1])} disabled={i <= 0} aria-label="Previous week">←</button>
      <label className="sr-only" htmlFor="week-select">Week</label>
      <select id="week-select" value={value} onChange={(e) => onChange(e.target.value)}>
        {[...weeks].reverse().map((w) => <option key={w} value={w}>{weekTitle(w)}</option>)}
      </select>
      <button type="button" className="icon-btn" onClick={() => onChange(weeks[i + 1])} disabled={i < 0 || i >= weeks.length - 1}
              aria-label="Next week">→</button>
    </div>
  );
}
