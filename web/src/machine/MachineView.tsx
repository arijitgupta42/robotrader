// Lazy-loaded entry for the 3D machine (three.js is most of the site's JavaScript, so the rest of the page
// does not wait for it).
import { useMemo } from "react";

import type { Stage, StageId, StageReading, WeekContext } from "../data/stages";
import { Machine } from "./Machine";
import { machineState } from "./machineState";

export default function MachineView({ ctx, readings, selected, onSelect }: {
  ctx: WeekContext;
  readings: { stage: Stage; reading: StageReading }[];
  selected: StageId | null;
  onSelect: (id: StageId | null) => void;
}) {
  const state = useMemo(() => machineState(ctx, readings), [ctx, readings]);
  return <Machine readings={readings} state={state} selected={selected} onSelect={onSelect} />;
}
