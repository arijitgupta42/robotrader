// Deep imports: the drei barrel also loads helpers (troika text) that start blob web workers, which the CSP forbids.
import { OrbitControls } from "@react-three/drei/core/OrbitControls";
import { PerformanceMonitor } from "@react-three/drei/core/PerformanceMonitor";
import { Html } from "@react-three/drei/web/Html";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";

import type { Stage, StageId, StageReading } from "../data/stages";
import { StatusBadge } from "../components/StatusBadge";
import { StageDetail } from "../components/PlainView";
import { BELT_Y, C, CENTER_X, framing, LAYOUT, Station, STATUS_COLOR, VIEW_DIR_ARRAY, type PartProps } from "./kit";
import type { MachineState } from "./machineState";
import {
  ChutePart, ClockPart, DripPart, EnginePart, GatePart, GaugesPart, HoppersPart, OreFlow, PressPart, SievePart, TanksPart, TubePart, VaultPart,
} from "./parts";

const STEP_SECONDS = 1.8;
const VIEW_DIR = new THREE.Vector3(...VIEW_DIR_ARRAY).normalize();

function usePrefersReducedMotion(): boolean {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

/** Moves the camera to the focused station (or the overview), then lets the viewer orbit freely. */
function CameraRig({ focus, panelOpen, reduced, nonce }: { focus: StageId | null; panelOpen: boolean; reduced: boolean; nonce: number }) {
  const { camera, size, controls, invalidate } = useThree();
  const goal = useRef<{ target: THREE.Vector3; position: THREE.Vector3 } | null>(null);

  useEffect(() => {
    const persp = camera as THREE.PerspectiveCamera;
    const { target, distance } = framing(focus, size.width / Math.max(size.height, 1), persp.fov, panelOpen && size.width > 640);
    const t = new THREE.Vector3(...target);
    goal.current = { target: t, position: t.clone().add(VIEW_DIR.clone().multiplyScalar(distance)) };
    invalidate();
  }, [focus, panelOpen, size.width, size.height, camera, invalidate, nonce]);

  useFrame(() => {
    const g = goal.current;
    const c = controls as unknown as OrbitControlsImpl | null;
    if (!g || !c) return;
    const k = reduced ? 1 : 0.07;
    c.target.lerp(g.target, k);
    camera.position.lerp(g.position, k);
    c.update();
    if (camera.position.distanceTo(g.position) < 0.02 && c.target.distanceTo(g.target) < 0.02) goal.current = null;
    else invalidate();
  });
  return null;
}

function Floor() {
  const back = { x0: LAYOUT.schedule.x - 2.2, x1: LAYOUT.signals.x + 2.8, z0: LAYOUT.reddit.z - 1.8, z1: LAYOUT.feeds.z + 2.6 };
  const front = { x0: LAYOUT.email.x - 1.8, x1: LAYOUT.prices.x + 2.9, z0: LAYOUT.prices.z - 2.6, z1: LAYOUT.prices.z + 2.6 };
  const belt = { x0: LAYOUT.feeds.x - LAYOUT.feeds.w / 2, x1: LAYOUT.llm.x - 1.6 };
  return (
    <group>
      {[back, front].map((f, i) => (
        <group key={i}>
          <mesh position={[(f.x0 + f.x1) / 2, -0.06, (f.z0 + f.z1) / 2]}>
            <boxGeometry args={[f.x1 - f.x0, 0.12, f.z1 - f.z0]} />
            <meshStandardMaterial color={i === 0 ? "#383d44" : "#3a4048"} metalness={0.2} roughness={0.9} />
          </mesh>
          <Html position={[f.x0 + 0.3, 0.05, i === 0 ? f.z0 + 0.6 : f.z0 + 0.5]} zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
            <div className="m-zone">{i === 0 ? "sector-scout Lambda →" : "← stock-selection Lambda"}</div>
          </Html>
        </group>
      ))}
      {/* the scout's conveyor: hoppers -> sieve -> engine */}
      <mesh position={[(belt.x0 + belt.x1) / 2, BELT_Y, LAYOUT.feeds.z]}>
        <boxGeometry args={[belt.x1 - belt.x0, 0.1, 1.1]} />
        <meshStandardMaterial color="#1f2226" roughness={0.95} />
      </mesh>
      {/* stock selection's rail: tanks -> gauges -> gate -> vault -> chute */}
      <mesh position={[(LAYOUT.prices.x + LAYOUT.email.x) / 2, 0.45, LAYOUT.prices.z - 2.5]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.07, 0.07, LAYOUT.prices.x - LAYOUT.email.x, 10]} />
        <meshStandardMaterial color={C.copper} metalness={0.7} roughness={0.35} />
      </mesh>
    </group>
  );
}

export function Machine({ readings, state, selected, onSelect }: {
  readings: { stage: Stage; reading: StageReading }[];
  state: MachineState;
  selected: StageId | null;
  onSelect: (id: StageId | null) => void;
}) {
  const reduced = usePrefersReducedMotion();
  const last = readings.length - 1;
  const [cursor, setCursor] = useState(reduced ? last : 0);
  const [playing, setPlaying] = useState(!reduced);
  const [hovered, setHovered] = useState<StageId | null>(null);
  const [low, setLow] = useState(false);
  const [nonce, setNonce] = useState(0);
  const wrap = useRef<HTMLDivElement>(null);
  const [compact, setCompact] = useState(false);

  // Replay from the start whenever the week changes.
  const weekKey = readings.map((r) => r.reading.headline).join("|");
  useEffect(() => {
    setCursor(reduced ? last : 0);
    setPlaying(!reduced);
  }, [weekKey, reduced, last]);

  useEffect(() => {
    if (!playing) return;
    if (cursor >= last) { setPlaying(false); return; }
    const id = window.setTimeout(() => setCursor((c) => Math.min(last, c + 1)), STEP_SECONDS * 1000);
    return () => window.clearTimeout(id);
  }, [playing, cursor, last]);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setCompact(e.contentRect.width < 720));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && selected) onSelect(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, onSelect]);

  const narrow = compact;
  const playingStage = readings[cursor]?.stage.id ?? null;
  const focus: StageId | null = selected ?? (narrow && (playing || cursor < last) ? playingStage : null);

  const props = (i: number): PartProps => {
    const status = readings[i].reading.status;
    return { status, running: i <= cursor && (status === "ok" || status === "warn"), active: i === cursor && playing, reduced, low };
  };
  const byId = useMemo(() => Object.fromEntries(readings.map((r, i) => [r.stage.id, i])) as Record<StageId, number>, [readings]);
  const P = (id: StageId) => props(byId[id]);

  const station = (id: StageId, child: React.ReactNode) => {
    const i = byId[id];
    const { stage, reading } = readings[i];
    return (
      <Station key={id} id={id} index={i} title={stage.title} status={reading.status} hovered={hovered === id} selected={selected === id}
               active={i === cursor && playing} compactLabels onHover={setHovered} onSelect={(s) => onSelect(s === selected ? null : s)}>
        {child}
      </Station>
    );
  };

  const hoverInfo = hovered ? readings[byId[hovered]] : null;
  const selectedInfo = selected ? readings[byId[selected]] : null;

  return (
    <div className="machine" ref={wrap}>
      <div className="machine-bar">
        <div className="machine-controls" role="group" aria-label="Playback">
          <button type="button" className="icon-btn" onClick={() => { setPlaying(false); setCursor((c) => Math.max(0, c - 1)); }}
                  disabled={cursor <= 0} aria-label="Previous stage">◀</button>
          <button type="button" className="icon-btn play" onClick={() => {
            if (playing) setPlaying(false);
            else { if (cursor >= last) setCursor(0); setPlaying(true); }
          }}>{playing ? "Pause" : cursor >= last ? "Replay" : "Play"}</button>
          <button type="button" className="icon-btn" onClick={() => { setPlaying(false); setCursor((c) => Math.min(last, c + 1)); }}
                  disabled={cursor >= last} aria-label="Next stage">▶</button>
          <button type="button" className="text-btn" onClick={() => { onSelect(null); setNonce((n) => n + 1); }}>Overview</button>
        </div>
        <div className="machine-now" aria-live="polite">
          <span className="muted small">Stage {cursor + 1} of {readings.length}:</span> <b>{readings[cursor]?.stage.title}</b>
        </div>
      </div>

      <div className="machine-stage">
        <Canvas className="machine-canvas" dpr={low ? 1 : [1, 2]} frameloop={reduced ? "demand" : "always"}
                camera={{ position: [CENTER_X, 40, 40], fov: 35, near: 0.5, far: 400 }} gl={{ antialias: !low, alpha: true }}
                onPointerMissed={() => setHovered(null)} aria-hidden="true">
          <PerformanceMonitor onDecline={() => setLow(true)} />
          <hemisphereLight args={["#e8eefc", "#2a2622", 0.9]} />
          <directionalLight position={[10, 22, 14]} intensity={1.6} />
          <directionalLight position={[-18, 10, -10]} intensity={0.45} color="#b8c8ff" />
          <OrbitControls makeDefault enableDamping={!reduced} minDistance={6} maxDistance={110} maxPolarAngle={Math.PI * 0.47}
                         target={[CENTER_X, 1, 0]} />
          <CameraRig focus={focus} panelOpen={selected !== null} reduced={reduced} nonce={nonce} />
          <Floor />
          {station("schedule", <ClockPart p={P("schedule")} retry={state.trigger === "HourlyRetry"} />)}
          {station("feeds", <HoppersPart p={P("feeds")} feeds={state.feeds} />)}
          {station("reddit", <DripPart p={P("reddit")} reddit={state.reddit} />)}
          {station("sieve", <SievePart p={P("sieve")} sieve={state.sieve} />)}
          {station("llm", <EnginePart p={P("llm")} cylinders={state.cylinders} spareFires={state.spareFires} />)}
          {station("signals", <PressPart p={P("signals")} dies={state.dies} />)}
          {station("handoff", <TubePart p={P("handoff")} sent={state.tube} />)}
          {station("prices", <TanksPart p={P("prices")} tanks={state.tanks} />)}
          {station("analysis", <GaugesPart p={P("analysis")} gauges={state.gauges} />)}
          {station("selection", <GatePart p={P("selection")} lanes={state.lanes} />)}
          {station("portfolio", <VaultPart p={P("portfolio")} scale={state.scale} trades={state.trades} />)}
          {station("email", <ChutePart p={P("email")} sent={state.mail} />)}
          <OreFlow p={P("feeds")} feeds={state.feeds} sieve={state.sieve} />
        </Canvas>

        {hoverInfo && !selectedInfo && (
          <div className="m-tip" role="status">
            <div className="small muted">{String(byId[hoverInfo.stage.id] + 1).padStart(2, "0")} · {hoverInfo.stage.machinePart}</div>
            <div><b>{hoverInfo.stage.title}</b>: {hoverInfo.stage.component}</div>
            <div className="small" style={{ marginTop: 4 }}>{hoverInfo.reading.headline}</div>
            <div className="small muted" style={{ marginTop: 4 }}>Click for the code and this week's numbers.</div>
          </div>
        )}

        {selectedInfo && (
          <aside className="m-panel" aria-label={`${selectedInfo.stage.title} details`}>
            <div className="m-panel-head">
              <div>
                <div className="small muted">{String(byId[selectedInfo.stage.id] + 1).padStart(2, "0")} · {selectedInfo.stage.lambda} Lambda</div>
                <h3>{selectedInfo.stage.title}</h3>
              </div>
              <button type="button" className="icon-btn" onClick={() => onSelect(null)} aria-label="Close details">✕</button>
            </div>
            <StatusBadge status={selectedInfo.reading.status} />
            <p className="m-headline">{selectedInfo.reading.headline}</p>
            <StageDetail stage={selectedInfo.stage} reading={selectedInfo.reading} />
          </aside>
        )}
      </div>

      <nav className="m-strip" aria-label="Pipeline stages">
        {readings.map(({ stage, reading }, i) => (
          <button key={stage.id} type="button" aria-pressed={selected === stage.id} aria-current={i === cursor ? "step" : undefined}
                  className={`m-chip${i <= cursor ? " reached" : ""}`} onClick={() => onSelect(selected === stage.id ? null : stage.id)}
                  onMouseEnter={() => setHovered(stage.id)} onMouseLeave={() => setHovered(null)}
                  onFocus={() => setHovered(stage.id)} onBlur={() => setHovered(null)}>
            <span className="dot" style={{ background: STATUS_COLOR[reading.status] }} aria-hidden="true" />
            <span className="m-chip-num">{String(i + 1).padStart(2, "0")}</span> {stage.title}
            <span className="sr-only">, {reading.status === "ok" ? "ran" : reading.status === "warn" ? "ran with problems" : reading.status === "fail" ? "failed or did not run" : "no detail recorded"}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
