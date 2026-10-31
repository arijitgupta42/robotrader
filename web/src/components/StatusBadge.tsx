import { STATUS_TEXT, type StageStatus } from "../data/stages";

const ICON: Record<StageStatus, string> = { ok: "✓", warn: "!", fail: "✕", idle: "–" };

/** Status is always an icon plus words, never colour alone. */
export function StatusBadge({ status }: { status: StageStatus }) {
  return (
    <span className={`status ${status}`}>
      <span className="icon" aria-hidden="true">{ICON[status]}</span>
      {STATUS_TEXT[status]}
    </span>
  );
}
