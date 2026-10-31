import type { Stage, StageReading } from "../data/stages";
import { StatusBadge } from "./StatusBadge";

/** The pipeline as an ordered, accessible list: the same stages and numbers as the 3D machine. */
export function PlainView({ readings, openStage, onOpen }: {
  readings: { stage: Stage; reading: StageReading }[];
  openStage: string | null;
  onOpen: (id: string | null) => void;
}) {
  return (
    <ol className="stages">
      {readings.map(({ stage, reading }, i) => (
        <li key={stage.id}>
          <details className="stage" open={openStage === stage.id}
                   onToggle={(e) => {
                     const open = (e.currentTarget as HTMLDetailsElement).open;
                     if (open && openStage !== stage.id) onOpen(stage.id);
                     if (!open && openStage === stage.id) onOpen(null);
                   }}>
            <summary>
              <span className="num">{String(i + 1).padStart(2, "0")}</span>
              <span>
                <span className="lambda">{stage.lambda} Lambda</span>
                <div className="title">{stage.title}</div>
              </span>
              <StatusBadge status={reading.status} />
              <span className="headline">{reading.headline}</span>
            </summary>
            <StageDetail stage={stage} reading={reading} />
          </details>
        </li>
      ))}
    </ol>
  );
}

export function StageDetail({ stage, reading }: { stage: Stage; reading: StageReading }) {
  return (
    <div className="body">
      <p style={{ marginTop: 0 }}>{stage.description}</p>
      <dl className="facts">
        <dt>In the machine</dt><dd>{stage.machinePart}</dd>
        <dt>What it really is</dt><dd>{stage.component}</dd>
        <dt>Code</dt>
        <dd>{stage.code.map((c) => <div key={c.file + c.symbol}><code>{c.file}</code> · <code>{c.symbol}</code></div>)}</dd>
        {reading.facts.map((f) => <FactRow key={f.label} label={f.label} value={f.value} />)}
      </dl>
      {reading.items && reading.items.length > 0 && (
        <ul className="items">
          {reading.items.map((it, i) => (
            <li key={`${it.label}-${i}`}>
              <span><span className={`dot ${it.status ?? "idle"}`} aria-hidden="true" />{it.label}</span>
              <span className="v">{it.value}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function FactRow({ label, value }: { label: string; value: string }) {
  return <><dt>{label}</dt><dd>{value}</dd></>;
}
