// Shared pieces of the 3D machine: layout, palette, the station wrapper and small helpers.
// Deep imports: the drei barrel also loads helpers (troika text) that start blob web workers, which the CSP forbids.
import { Html } from "@react-three/drei/web/Html";
import type { ThreeEvent } from "@react-three/fiber";
import type { ReactNode } from "react";

import type { StageId, StageStatus } from "../data/stages";

/**
 * Where each station stands on the factory floor.  The line is a U: the scout runs left to right along the
 * back row (z < 0), the pneumatic tube turns the corner at the right, and stock selection runs right to left
 * along the front row (z > 0), ending at the mail chute.  `flow` is the direction work moves through a station.
 */
export const LAYOUT: Record<StageId, { x: number; z: number; w: number; d: number; flow: 1 | -1 }> = {
  schedule:  { x: -15,  z: -4.2, w: 3.2, d: 3,   flow: 1 },
  feeds:     { x: -9,   z: -4.2, w: 6.6, d: 3.6, flow: 1 },
  reddit:    { x: -3,   z: -8.6, w: 3,   d: 2.6, flow: 1 },
  sieve:     { x: -3,   z: -4.2, w: 3,   d: 3,   flow: 1 },
  llm:       { x: 3,    z: -4.2, w: 4.6, d: 3.4, flow: 1 },
  signals:   { x: 9.6,  z: -4.2, w: 4.8, d: 3.6, flow: 1 },
  handoff:   { x: 15.4, z: 0,    w: 2.6, d: 7,   flow: 1 },
  prices:    { x: 9.6,  z: 4.4,  w: 5,   d: 4.4, flow: -1 },
  analysis:  { x: 3.6,  z: 4.4,  w: 3.6, d: 2.6, flow: -1 },
  selection: { x: -2,   z: 4.4,  w: 4,   d: 4,   flow: -1 },
  portfolio: { x: -8.4, z: 4.4,  w: 5,   d: 3.6, flow: -1 },
  email:     { x: -14.2, z: 4.4, w: 2.6, d: 2.6, flow: -1 },
};

export const BELT_Y = 0.55;
export const CENTER_X = 0.2;
export const HALF_WIDTH = 18.5;   // half the line's width, for framing the overview
export const HALF_DEPTH = 11;
export const VIEW_DIR_ARRAY: [number, number, number] = [0, 1.1, 1];   // the camera looks down at about 48 degrees

/**
 * Where the camera looks and how far back it stands: the whole line (focus = null), or one station.
 * Pure, so the framing is tested without WebGL.  On a portrait screen the whole line would be tiny, so the
 * overview is capped and the machine follows the playback instead (see Machine.tsx).
 */
export function framing(focus: StageId | null, aspect: number, fovDeg: number, panelOnRight = false): { target: [number, number, number]; distance: number } {
  const tanHalf = Math.tan((fovDeg * Math.PI) / 360);
  if (focus) {
    const l = LAYOUT[focus];
    const distance = Math.max(9, (Math.max(l.w, 4) / 2 + 2.5) / (tanHalf * Math.min(aspect, 1.6)));
    // With the details panel over the right of the view, look a little to the right so the station sits in the clear left part.
    const shift = panelOnRight ? distance * tanHalf * aspect * 0.42 : 0;
    return { target: [l.x + shift, 1.2, l.z], distance };
  }
  return { target: [CENTER_X, 1, 0], distance: Math.min(80, Math.max((HALF_WIDTH * 1.04) / (tanHalf * aspect), (HALF_DEPTH * 0.62) / tanHalf)) };
}

export const STATUS_COLOR: Record<StageStatus, string> = { ok: "#0ca30c", warn: "#fab219", fail: "#d03b3b", idle: "#898781" };
export const STATUS_ICON: Record<StageStatus, string> = { ok: "✓", warn: "!", fail: "✕", idle: "–" };

export const C = {
  steel: "#8d939c",
  darkSteel: "#4a4f57",
  iron: "#33373d",
  brass: "#c9a227",
  copper: "#b87333",
  plinth: "#2c3036",
  ore: "#d9b56a",
  blue: "#3987e5",
  orange: "#e8743b",
  glass: "#9fd3ff",
  paper: "#f4f1e8",
};

export interface PartProps {
  status: StageStatus;
  running: boolean;     // this stage has run in the playback and has something to show
  active: boolean;      // the stage the playback is on now
  reduced: boolean;     // prefers-reduced-motion: static poses only
  low: boolean;         // low detail for weak devices
}

/** Time for animations: frozen at a fixed pose when the part is not running or motion is reduced. */
export function partTime(clockTime: number, p: PartProps, still = 0.6): number {
  return p.running && !p.reduced ? clockTime : still;
}

export function Station({ id, index, title, status, hovered, selected, active, compactLabels, onHover, onSelect, children }: {
  id: StageId;
  index: number;
  title: string;
  status: StageStatus;
  hovered: boolean;
  selected: boolean;
  active: boolean;
  compactLabels: boolean;
  onHover: (id: StageId | null) => void;
  onSelect: (id: StageId) => void;
  children: ReactNode;
}) {
  const { x, z, w, d } = LAYOUT[id];
  const lit = hovered || selected || active;
  const over = (e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); onHover(id); document.body.style.cursor = "pointer"; };
  const out = () => { onHover(null); document.body.style.cursor = ""; };
  const click = (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); onSelect(id); };
  return (
    <group position={[x, 0, z]} onPointerOver={over} onPointerOut={out} onClick={click}>
      {/* plinth */}
      <mesh position={[0, 0.12, 0]}>
        <boxGeometry args={[w, 0.24, d]} />
        <meshStandardMaterial color={C.plinth} metalness={0.3} roughness={0.8}
                              emissive={lit ? (selected ? "#3987e5" : "#5b7fae") : "#000000"} emissiveIntensity={selected ? 0.45 : lit ? 0.25 : 0} />
      </mesh>
      {/* status lamp on the front-left corner */}
      <mesh position={[-w / 2 + 0.25, 0.4, d / 2 - 0.25]}>
        <sphereGeometry args={[0.14, 16, 12]} />
        <meshStandardMaterial color={STATUS_COLOR[status]} emissive={STATUS_COLOR[status]} emissiveIntensity={status === "idle" ? 0.1 : 0.9} />
      </mesh>
      {children}
      <Html position={[0, 0, d / 2 + 0.55]} center zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
        <div className={`m-label ${status}${selected ? " selected" : ""}`}>
          <span className="m-icon" aria-hidden="true">{STATUS_ICON[status]}</span>
          <span className="m-num">{String(index + 1).padStart(2, "0")}</span>
          {(lit || !compactLabels) && <span className="m-title">{title}</span>}
        </div>
      </Html>
    </group>
  );
}

/** Deterministic pseudo-random number in [0, 1) for particle i (so a re-render never reshuffles). */
export function rand(i: number, salt = 0): number {
  const s = Math.sin(i * 127.1 + salt * 311.7) * 43758.5453;
  return s - Math.floor(s);
}
