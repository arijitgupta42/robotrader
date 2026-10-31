// The twelve stations of the machine.  Each draws in its station's local frame (origin at the centre of its
// plinth, floor at y = 0.24) and animates only from the numbers it is given.
import { useFrame } from "@react-three/fiber";
import { useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";

import { C, LAYOUT, partTime, rand, STATUS_COLOR, type PartProps } from "./kit";
import { scaleTilt, type MachineState } from "./machineState";

const FLOOR = 0.24;
const metal = (color: string, rough = 0.45, metalness = 0.75) => (
  <meshStandardMaterial color={color} metalness={metalness} roughness={rough} />
);

function Gear({ radius, teeth, thickness = 0.12, color = C.brass, groupRef }: {
  radius: number; teeth: number; thickness?: number; color?: string; groupRef?: React.Ref<THREE.Group>;
}) {
  return (
    <group ref={groupRef}>
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[radius, radius, thickness, 32]} />
        {metal(color, 0.35)}
      </mesh>
      {Array.from({ length: teeth }, (_, i) => {
        const a = (i / teeth) * Math.PI * 2;
        return (
          <mesh key={i} position={[Math.cos(a) * radius, Math.sin(a) * radius, 0]} rotation={[0, 0, a]}>
            <boxGeometry args={[radius * 0.32, radius * 0.22, thickness]} />
            {metal(color, 0.35)}
          </mesh>
        );
      })}
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[radius * 0.25, radius * 0.25, thickness * 1.6, 16]} />
        {metal(C.iron)}
      </mesh>
    </group>
  );
}

// 01 ------------------------------------------------------------------------------------------------
export function ClockPart({ p, retry }: { p: PartProps; retry: boolean }) {
  const hand = useRef<THREE.Mesh>(null);
  const minute = useRef<THREE.Mesh>(null);
  const gear = useRef<THREE.Group>(null);
  const small = useRef<THREE.Group>(null);
  useFrame((s) => {
    const t = partTime(s.clock.elapsedTime, p);
    if (hand.current) hand.current.rotation.z = -t * 0.5;
    if (minute.current) minute.current.rotation.z = -t * 3;
    if (gear.current) gear.current.rotation.z = t * 0.8;
    if (small.current) small.current.rotation.z = -t * 1.6;
  });
  return (
    <group position={[0, FLOOR, 0]}>
      <mesh position={[0, 0.9, -0.4]}><boxGeometry args={[0.5, 1.8, 0.5]} />{metal(C.darkSteel)}</mesh>
      <group position={[0, 2.1, -0.1]}>
        <mesh rotation={[Math.PI / 2, 0, 0]}><cylinderGeometry args={[0.95, 0.95, 0.16, 48]} />{metal(C.brass, 0.3)}</mesh>
        <mesh position={[0, 0, 0.09]}><circleGeometry args={[0.82, 48]} /><meshStandardMaterial color={C.paper} roughness={0.9} /></mesh>
        {Array.from({ length: 12 }, (_, i) => {
          const a = (i / 12) * Math.PI * 2;
          return <mesh key={i} position={[Math.sin(a) * 0.68, Math.cos(a) * 0.68, 0.1]}><boxGeometry args={[0.04, 0.12, 0.02]} /><meshStandardMaterial color={C.iron} /></mesh>;
        })}
        <mesh ref={hand} position={[0, 0, 0.12]}><boxGeometry args={[0.06, 0.5, 0.02]} /><meshStandardMaterial color={C.iron} /></mesh>
        <mesh ref={minute} position={[0, 0, 0.14]}><boxGeometry args={[0.035, 0.72, 0.02]} />
          <meshStandardMaterial color={retry ? STATUS_COLOR.warn : C.iron} /></mesh>
      </group>
      <group position={[0.95, 0.8, 0.2]}><Gear radius={0.42} teeth={10} groupRef={gear} /></group>
      <group position={[0.35, 0.35, 0.35]}><Gear radius={0.26} teeth={7} color={C.copper} groupRef={small} /></group>
    </group>
  );
}

// 02 ------------------------------------------------------------------------------------------------
/** Local position (in the feeds station) of hopper k of n: two rows above the belt. */
export function hopperPos(k: number, n: number): [number, number, number] {
  const perRow = Math.ceil(n / 2);
  const row = k < perRow ? 0 : 1;
  const col = row === 0 ? k : k - perRow;
  const span = LAYOUT.feeds.w - 0.8;
  const x = -span / 2 + (perRow <= 1 ? span / 2 : (col / (perRow - 1)) * span);
  return [x, 1.95 + row * 0.15, row === 0 ? -0.75 : 0.75];
}

export function HoppersPart({ p, feeds }: { p: PartProps; feeds: MachineState["feeds"] }) {
  const n = feeds.length;
  const shakes = useRef<(THREE.Group | null)[]>([]);
  useFrame((s) => {
    const t = partTime(s.clock.elapsedTime, p);
    shakes.current.forEach((g, k) => {
      if (!g) return;
      const f = feeds[k];
      g.rotation.z = f && f.ok ? Math.sin(t * 9 + k) * 0.04 * f.weight : 0.35;     // a failed hopper hangs jammed
    });
  });
  if (!n) {
    return (
      <group position={[0, FLOOR, 0]}>
        {Array.from({ length: 8 }, (_, k) => {
          const [x, y, z] = hopperPos(k, 8);
          return <mesh key={k} position={[x, y, z]} rotation={[Math.PI, 0, 0]}><coneGeometry args={[0.3, 0.6, 12, 1, true]} />{metal(C.darkSteel, 0.7, 0.4)}</mesh>;
        })}
      </group>
    );
  }
  const r = Math.min(0.32, (LAYOUT.feeds.w - 0.8) / Math.ceil(n / 2) * 0.48);
  return (
    <group position={[0, FLOOR, 0]}>
      {/* rack */}
      {[-1.25, 1.25].map((z) => (
        <mesh key={z} position={[0, 1.5, z]}><boxGeometry args={[LAYOUT.feeds.w - 0.2, 0.08, 0.08]} />{metal(C.darkSteel)}</mesh>
      ))}
      {[-1, 1].flatMap((sx) => [-1.25, 1.25].map((z) => (
        <mesh key={`${sx}${z}`} position={[sx * (LAYOUT.feeds.w / 2 - 0.15), 0.75, z]}><boxGeometry args={[0.1, 1.5, 0.1]} />{metal(C.darkSteel)}</mesh>
      )))}
      {feeds.map((f, k) => {
        const [x, y, z] = hopperPos(k, n);
        return (
          <group key={f.name} position={[x, y, z]} ref={(g) => { shakes.current[k] = g; }}>
            <mesh rotation={[Math.PI, 0, 0]}>
              <coneGeometry args={[r, r * 2.1, 12, 1, true]} />
              <meshStandardMaterial color={f.ok ? C.steel : STATUS_COLOR.fail} metalness={0.6} roughness={0.4} side={THREE.DoubleSide}
                                    emissive={f.ok ? "#000" : STATUS_COLOR.fail} emissiveIntensity={f.ok ? 0 : 0.35} />
            </mesh>
            {f.ok && <mesh position={[0, r * 0.75, 0]}><cylinderGeometry args={[r * 0.8, r * 0.8, 0.06 + f.weight * 0.2, 12]} /><meshStandardMaterial color={C.ore} roughness={0.9} /></mesh>}
          </group>
        );
      })}
    </group>
  );
}

// 03 ------------------------------------------------------------------------------------------------
export function DripPart({ p, reddit }: { p: PartProps; reddit: MachineState["reddit"] }) {
  const wheel = useRef<THREE.Mesh>(null);
  const drop = useRef<THREE.Mesh>(null);
  const rate = reddit && reddit.planned ? reddit.ok / reddit.planned : 0;
  useFrame((s) => {
    const t = partTime(s.clock.elapsedTime, p);
    if (wheel.current) wheel.current.rotation.z = t * 0.6 * rate;
    if (drop.current) {
      const ph = (t * (0.4 + rate * 0.6)) % 1;
      drop.current.position.y = 1.55 - ph * 1.1;
      drop.current.visible = rate > 0 && p.running && !p.reduced;
      drop.current.scale.setScalar(0.6 + Math.sin(ph * Math.PI) * 0.4);
    }
  });
  const lights = reddit?.planned ?? 7;
  return (
    <group position={[0, FLOOR, 0]}>
      <mesh position={[-0.6, 1.2, 0]}><cylinderGeometry args={[0.45, 0.45, 2.2, 24]} />{metal(C.copper, 0.35)}</mesh>
      <mesh position={[-0.6, 2.35, 0]}><sphereGeometry args={[0.45, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2]} />{metal(C.copper, 0.35)}</mesh>
      {Array.from({ length: lights }, (_, i) => {
        const req = i < (reddit?.ok ?? 0) ? "ok" : i < (reddit?.ok ?? 0) + (reddit?.throttled ?? 0) ? "fail" : "idle";
        return (
          <mesh key={i} position={[-0.6 + Math.sin((i / lights) * Math.PI - Math.PI / 2) * 0.47, 0.5 + i * 0.25, Math.cos((i / lights) * Math.PI - Math.PI / 2) * 0.47]}>
            <sphereGeometry args={[0.07, 10, 8]} />
            <meshStandardMaterial color={STATUS_COLOR[reddit ? req : "idle"]} emissive={STATUS_COLOR[reddit ? req : "idle"]} emissiveIntensity={reddit ? 0.8 : 0.1} />
          </mesh>
        );
      })}
      <mesh position={[0.15, 1.75, 0]} rotation={[0, 0, Math.PI / 2]}><cylinderGeometry args={[0.09, 0.09, 1.2, 12]} />{metal(C.steel)}</mesh>
      <mesh position={[0.7, 1.68, 0]}><cylinderGeometry args={[0.07, 0.04, 0.2, 12]} />{metal(C.steel)}</mesh>
      <mesh ref={wheel} position={[0.25, 2.05, 0]}><torusGeometry args={[0.22, 0.04, 8, 24]} /><meshStandardMaterial color={STATUS_COLOR.fail} metalness={0.4} roughness={0.5} /></mesh>
      <mesh ref={drop} position={[0.7, 1.4, 0]}><sphereGeometry args={[0.09, 12, 10]} /><meshStandardMaterial color={C.blue} emissive={C.blue} emissiveIntensity={0.4} /></mesh>
      {/* pipe to the Reddit cylinder at the back of the engine */}
      {(() => {
        const ex = LAYOUT.llm.x - LAYOUT.reddit.x - 0.45, ez = LAYOUT.llm.z - LAYOUT.reddit.z - LAYOUT.llm.d / 2;
        return (
          <>
            <mesh position={[(0.7 + ex) / 2, 0.45, 0]} rotation={[0, 0, Math.PI / 2]}><cylinderGeometry args={[0.08, 0.08, ex - 0.7, 10]} />{metal(C.copper, 0.4)}</mesh>
            <mesh position={[ex, 0.45, ez / 2]} rotation={[Math.PI / 2, 0, 0]}><cylinderGeometry args={[0.08, 0.08, ez, 10]} />{metal(C.copper, 0.4)}</mesh>
            <mesh position={[0.7, 0.45, 0]}><sphereGeometry args={[0.13, 12, 10]} />{metal(C.copper, 0.4)}</mesh>
            <mesh position={[ex, 0.45, 0]}><sphereGeometry args={[0.13, 12, 10]} />{metal(C.copper, 0.4)}</mesh>
          </>
        );
      })()}
    </group>
  );
}

// 04 ------------------------------------------------------------------------------------------------
export function SievePart({ p, sieve }: { p: PartProps; sieve: MachineState["sieve"] }) {
  const tray = useRef<THREE.Group>(null);
  useFrame((s) => {
    const t = partTime(s.clock.elapsedTime, p);
    if (tray.current) tray.current.position.x = Math.sin(t * 14) * 0.06;
  });
  const pile = sieve ? 0.25 + sieve.removed * 0.6 : 0.2;
  return (
    <group position={[0, FLOOR, 0]}>
      {[[-1.1, -1.1], [1.1, -1.1], [-1.1, 1.1], [1.1, 1.1]].map(([x, z]) => (
        <mesh key={`${x}${z}`} position={[x, 0.75, z]}><boxGeometry args={[0.1, 1.5, 0.1]} />{metal(C.darkSteel)}</mesh>
      ))}
      <group ref={tray} position={[0, 1.35, 0]}>
        <mesh><boxGeometry args={[2.3, 0.08, 2.3]} /><meshStandardMaterial color={C.steel} wireframe /></mesh>
        <mesh position={[0, 0.2, 0]}><boxGeometry args={[2.4, 0.4, 2.4]} /><meshStandardMaterial color={C.steel} metalness={0.6} roughness={0.4} transparent opacity={0.28} /></mesh>
      </group>
      <mesh position={[0.2, 0.2, 1.05]}><coneGeometry args={[pile * 0.9, pile, 16]} /><meshStandardMaterial color="#8a7a5c" roughness={1} /></mesh>
    </group>
  );
}

/** Ore: headlines dropping from the hoppers, riding the belt through the sieve (some fall to the reject pile) into the engine. */
export function OreFlow({ p, feeds, sieve }: { p: PartProps; feeds: MachineState["feeds"]; sieve: MachineState["sieve"] }) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const count = p.low ? 60 : 170;
  const dummy = useMemo(() => new THREE.Object3D(), []);
  const okFeeds = useMemo(() => feeds.map((f, k) => ({ ...f, k })).filter((f) => f.ok), [feeds]);
  const cumulative = useMemo(() => {
    let acc = 0;
    const total = okFeeds.reduce((s, f) => s + f.weight, 0) || 1;
    return okFeeds.map((f) => (acc += f.weight / total));
  }, [okFeeds]);
  const kept = sieve?.kept ?? 1;
  const fx = LAYOUT.feeds.x, fz = LAYOUT.feeds.z, sx = LAYOUT.sieve.x, sz = LAYOUT.sieve.z;
  const ex = LAYOUT.llm.x - 1.6, ez = LAYOUT.llm.z;

  useFrame((s) => {
    const m = mesh.current;
    if (!m) return;
    const t = p.reduced ? 0.5 : s.clock.elapsedTime;
    for (let i = 0; i < count; i++) {
      let show = p.running && okFeeds.length > 0;
      let x = 0, y = 0, z = 0;
      if (show) {
        const pick = rand(i, 3);
        const idx = Math.max(0, cumulative.findIndex((c) => c >= pick));
        const [hx, hy, hz] = hopperPos(okFeeds[idx].k, feeds.length);
        const ph = (t * 0.09 + rand(i, 1)) % 1;
        const discarded = rand(i, 2) > kept;
        if (ph < 0.14) {
          const q = ph / 0.14;
          x = fx + hx; z = fz + hz; y = FLOOR + hy - 0.3 - q * q * (hy - 0.6);
        } else if (ph < 0.55) {
          const q = (ph - 0.14) / 0.41;
          x = fx + hx + (sx - fx - hx) * q; z = fz + hz + (sz - fz - hz) * q; y = 0.7 + FLOOR * 0.2;
        } else if (discarded) {
          const q = Math.min(1, (ph - 0.55) / 0.12);
          x = sx + 0.2 * q; z = sz + 1.05 * q; y = 0.7 - q * 0.3;
          show = ph < 0.67;
        } else if (ph < 0.92) {
          const q = (ph - 0.55) / 0.37;
          x = sx + (ex - sx) * q; z = sz + (ez - sz) * q; y = 0.7;
        } else show = false;
      }
      dummy.position.set(x, y, z);
      dummy.scale.setScalar(show ? 1 : 0);
      dummy.updateMatrix();
      m.setMatrixAt(i, dummy.matrix);
    }
    m.instanceMatrix.needsUpdate = true;
  });
  return (
    <instancedMesh ref={mesh} args={[undefined, undefined, count]} frustumCulled={false}>
      <dodecahedronGeometry args={[0.09, 0]} />
      <meshStandardMaterial color={C.ore} roughness={0.8} metalness={0.2} />
    </instancedMesh>
  );
}

// 05 ------------------------------------------------------------------------------------------------
export function EnginePart({ p, cylinders, spareFires }: { p: PartProps; cylinders: MachineState["cylinders"]; spareFires: boolean }) {
  const pistons = useRef<(THREE.Mesh | null)[]>([]);
  const fly = useRef<THREE.Group>(null);
  const all = [...cylinders.map((c) => ({ ...c, spare: false })), { name: "spare" as const, ran: spareFires, ok: spareFires, fallback: true, model: null, spare: true }];
  useFrame((s) => {
    const t = partTime(s.clock.elapsedTime, p);
    all.forEach((c, i) => {
      const m = pistons.current[i];
      if (!m) return;
      const moving = c.ran && c.ok && (!c.spare || spareFires);
      m.position.y = 1.95 + (moving ? Math.sin(t * 6 + i * 1.7) * 0.28 : -0.2);
    });
    if (fly.current) fly.current.rotation.z = t * 3;
  });
  return (
    <group position={[0, FLOOR, 0]}>
      <mesh position={[0, 0.65, 0]}><boxGeometry args={[3.6, 1.3, 2]} />{metal(C.darkSteel, 0.5)}</mesh>
      {all.map((c, i) => {
        const x = -1.35 + i * 0.9;
        const color = !c.ran ? C.iron : !c.ok ? STATUS_COLOR.fail : c.spare ? STATUS_COLOR.warn : c.fallback ? STATUS_COLOR.warn : C.steel;
        return (
          <group key={c.name} position={[x, 0, 0]}>
            <mesh position={[0, 1.55, 0]}><cylinderGeometry args={[0.32, 0.32, 0.6, 24, 1, true]} />
              <meshStandardMaterial color={color} metalness={0.7} roughness={0.35} side={THREE.DoubleSide}
                                    emissive={c.ran && (!c.ok || c.fallback) ? color : "#000"} emissiveIntensity={0.25} /></mesh>
            {c.spare && <mesh position={[0, 1.27, 0]}><torusGeometry args={[0.34, 0.03, 8, 24]} />{metal(C.brass)}</mesh>}
            <mesh ref={(m) => { pistons.current[i] = m; }} position={[0, 1.8, 0]}><cylinderGeometry args={[0.26, 0.26, 0.5, 20]} />{metal(C.steel, 0.25)}</mesh>
          </group>
        );
      })}
      <group ref={fly} position={[2.05, 0.75, 0]} rotation={[0, Math.PI / 2, 0]}>
        <mesh rotation={[Math.PI / 2, 0, 0]}><cylinderGeometry args={[0.6, 0.6, 0.14, 32]} />{metal(C.iron, 0.4)}</mesh>
        <mesh position={[0.4, 0, 0.08]}><boxGeometry args={[0.2, 0.2, 0.05]} />{metal(C.brass)}</mesh>
      </group>
    </group>
  );
}

// 06 ------------------------------------------------------------------------------------------------
export function PressPart({ p, dies }: { p: PartProps; dies: MachineState["dies"] }) {
  const head = useRef<THREE.Group>(null);
  const cols = 8;
  useFrame((s) => {
    const t = partTime(s.clock.elapsedTime, p, 0);
    if (head.current) {
      const ph = (t * 0.7) % 1;
      head.current.position.y = 2.55 - (ph < 0.15 ? ph / 0.15 : ph < 0.3 ? 1 - (ph - 0.15) / 0.15 : 0) * 0.85;
    }
  });
  return (
    <group position={[0, FLOOR, 0]}>
      {[-2.1, 2.1].map((x) => <mesh key={x} position={[x, 1.5, -1.3]}><boxGeometry args={[0.25, 3, 0.25]} />{metal(C.darkSteel)}</mesh>)}
      <mesh position={[0, 3.05, -1.3]}><boxGeometry args={[4.5, 0.3, 0.3]} />{metal(C.darkSteel)}</mesh>
      <group ref={head} position={[0, 2.55, -0.2]}>
        <mesh><boxGeometry args={[4, 0.18, 2.6]} />
          <meshStandardMaterial color={C.steel} metalness={0.7} roughness={0.3} transparent opacity={0.45} depthWrite={false} /></mesh>
        <mesh position={[0, 0.5, -0.4]}><cylinderGeometry args={[0.16, 0.16, 1, 12]} />{metal(C.steel, 0.2)}</mesh>
      </group>
      {dies.map((d, i) => {
        const col = i % cols, row = Math.floor(i / cols);
        const x = -1.75 + col * 0.5, z = -1.2 + row * 0.6;
        const lit = d.lit;
        return (
          <mesh key={d.sector} position={[x, 0.18 + (lit ? 0.1 + d.confidence * 0.2 : 0), z]}>
            <boxGeometry args={[0.38, lit ? 0.3 + d.confidence * 0.4 : 0.2, 0.42]} />
            <meshStandardMaterial color={lit ? C.blue : C.iron} metalness={0.5} roughness={0.4}
                                  emissive={lit ? C.blue : "#000"} emissiveIntensity={lit ? 0.3 + d.confidence * 0.9 : 0} />
          </mesh>
        );
      })}
    </group>
  );
}

// 07 ------------------------------------------------------------------------------------------------
export function TubePart({ p, sent }: { p: PartProps; sent: boolean }) {
  const capsule = useRef<THREE.Mesh>(null);
  const curve = useMemo(() => new THREE.CatmullRomCurve3([
    new THREE.Vector3(-3.4, 1.1, LAYOUT.signals.z), new THREE.Vector3(-0.6, 2.4, -3.2), new THREE.Vector3(0.9, 3.0, 0),
    new THREE.Vector3(-0.6, 2.4, 3.2), new THREE.Vector3(-3.4, 1.1, LAYOUT.prices.z)]), []);
  const geom = useMemo(() => new THREE.TubeGeometry(curve, 48, 0.24, 16, false), [curve]);
  useFrame((s) => {
    const t = partTime(s.clock.elapsedTime, p, 0.5);
    if (capsule.current) {
      const u = (t * 0.45) % 1;
      capsule.current.position.copy(curve.getPointAt(u));
      capsule.current.lookAt(curve.getPointAt(Math.min(1, u + 0.01)));
      capsule.current.visible = sent;
    }
  });
  return (
    <group position={[0, FLOOR, 0]}>
      <mesh geometry={geom}><meshStandardMaterial color={C.glass} transparent opacity={0.28} roughness={0.1} metalness={0.1} depthWrite={false} /></mesh>
      <mesh ref={capsule} rotation={[0, 0, 0]}><capsuleGeometry args={[0.13, 0.32, 6, 12]} />
        <meshStandardMaterial color={C.brass} metalness={0.8} roughness={0.25} emissive={C.brass} emissiveIntensity={0.2} /></mesh>
    </group>
  );
}

// 08 ------------------------------------------------------------------------------------------------
export function TanksPart({ p, tanks }: { p: PartProps; tanks: MachineState["tanks"] }) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const piston = useRef<THREE.Mesh>(null);
  const total = tanks ? (p.low ? Math.min(tanks.total, 240) : tanks.total) : 120;
  const flagged = tanks ? Math.round((tanks.flagged / Math.max(tanks.total, 1)) * total) : 0;
  const cols = Math.ceil(Math.sqrt(total * 1.25));
  const dummy = useMemo(() => new THREE.Object3D(), []);
  const spacing = Math.min(0.16, 3.6 / cols);
  useLayoutEffect(() => {
    const m = mesh.current;
    if (!m) return;
    const ok = new THREE.Color(tanks ? C.blue : C.iron);
    const bad = new THREE.Color(STATUS_COLOR.fail);
    // flagged tanks (no data, stale, Price Anomaly, too little history) are scattered through the grid
    for (let i = 0; i < total; i++) m.setColorAt(i, rand(i, 7) < flagged / Math.max(total, 1) ? bad : ok);
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
  }, [total, flagged, tanks]);
  useFrame((s) => {
    const m = mesh.current;
    const t = partTime(s.clock.elapsedTime, p, 99);
    if (piston.current) piston.current.position.y = 1.9 + Math.sin(t * 5) * (p.running ? 0.25 : 0);
    if (!m) return;
    const rows = Math.ceil(total / cols);
    for (let i = 0; i < total; i++) {
      const col = i % cols, row = Math.floor(i / cols);
      const fill = tanks ? Math.min(1, Math.max(0.05, (t * 0.5 - (col / cols) * 0.8))) : 0.05;
      dummy.position.set(-0.6 + (col - cols / 2) * spacing, 0.04 + fill * 0.25, (row - rows / 2) * spacing);
      dummy.scale.set(1, Math.max(0.05, fill), 1);
      dummy.updateMatrix();
      m.setMatrixAt(i, dummy.matrix);
    }
    m.instanceMatrix.needsUpdate = true;
  });
  return (
    <group position={[0, FLOOR, 0]}>
      <instancedMesh ref={mesh} args={[undefined, undefined, total]} frustumCulled={false}>
        <cylinderGeometry args={[spacing * 0.38, spacing * 0.38, 0.5, 8]} />
        <meshStandardMaterial metalness={0.4} roughness={0.45} />
      </instancedMesh>
      <group position={[2.05, 0, 0]}>
        <mesh position={[0, 0.8, 0]}><cylinderGeometry args={[0.35, 0.4, 1.6, 20]} />{metal(C.copper, 0.35)}</mesh>
        <mesh ref={piston} position={[0, 1.9, 0]}><cylinderGeometry args={[0.08, 0.08, 1, 10]} />{metal(C.steel, 0.2)}</mesh>
        <mesh position={[-0.5, 0.3, 0]} rotation={[0, 0, Math.PI / 2]}><cylinderGeometry args={[0.07, 0.07, 1, 10]} />{metal(C.copper)}</mesh>
      </group>
    </group>
  );
}

// 09 ------------------------------------------------------------------------------------------------
export function GaugesPart({ p, gauges }: { p: PartProps; gauges: MachineState["gauges"] }) {
  const needles = useRef<(THREE.Group | null)[]>([]);
  const values = gauges ? [gauges.A, gauges.B, gauges.C] : [0, 0, 0];
  useFrame((s) => {
    const t = partTime(s.clock.elapsedTime, p, 0);
    needles.current.forEach((n, i) => {
      if (!n) return;
      const target = -2.1 + Math.min(1, values[i] * 1.6) * 4.2;     // a sweep of about 240 degrees
      const settle = p.reduced ? 1 : Math.min(1, t * 0.6);
      n.rotation.z = -(-2.1 + (target + 2.1) * settle) + Math.sin(t * 7 + i) * 0.02 * (p.running ? 1 : 0);
    });
  });
  return (
    <group position={[0, FLOOR, 0]}>
      <mesh position={[0, 0.9, -0.3]}><boxGeometry args={[3.3, 1.8, 0.3]} />{metal(C.darkSteel)}</mesh>
      {["A", "B", "C"].map((g, i) => (
        <group key={g} position={[-1.05 + i * 1.05, 1.05, -0.1]}>
          <mesh rotation={[Math.PI / 2, 0, 0]}><cylinderGeometry args={[0.45, 0.45, 0.12, 32]} />{metal(C.brass, 0.3)}</mesh>
          <mesh position={[0, 0, 0.065]}><circleGeometry args={[0.39, 32]} /><meshStandardMaterial color={C.paper} /></mesh>
          <group ref={(n) => { needles.current[i] = n; }} position={[0, 0, 0.09]}>
            <mesh position={[0, 0.15, 0]}><boxGeometry args={[0.035, 0.32, 0.02]} /><meshStandardMaterial color={i === 0 ? STATUS_COLOR.ok : i === 1 ? C.blue : C.iron} /></mesh>
          </group>
          <mesh position={[0, 0, 0.1]}><sphereGeometry args={[0.04, 8, 8]} /><meshStandardMaterial color={C.iron} /></mesh>
        </group>
      ))}
    </group>
  );
}

// 10 ------------------------------------------------------------------------------------------------
export function GatePart({ p, lanes }: { p: PartProps; lanes: MachineState["lanes"] }) {
  const tokens = useRef<(THREE.Mesh | null)[]>([]);
  const shown = lanes.slice(0, 6);
  const laneZ = (i: number) => (shown.length <= 1 ? 0 : -1.5 + (i / (shown.length - 1)) * 3);
  const items = shown.flatMap((l, i) => [
    ...l.picks.map((tk, j) => ({ lane: i, pick: true, key: `${l.sector}-${tk}`, j })),
    ...Array.from({ length: Math.min(3, Math.max(0, l.candidates - l.picks.length)) }, (_, j) => ({ lane: i, pick: false, key: `${l.sector}-r${j}`, j })),
  ]);
  useFrame((s) => {
    const t = partTime(s.clock.elapsedTime, p, 2.2);
    items.forEach((it, k) => {
      const m = tokens.current[k];
      if (!m) return;
      const ph = ((t * 0.35 + it.j * 0.27 + it.lane * 0.11) % 1);
      const dir = LAYOUT.selection.flow;                                             // the front row runs right to left
      if (it.pick) {
        m.position.x = dir * (-1.8 + ph * 3.6);
      } else {
        m.position.x = dir * (-1.8 + Math.min(ph, 0.45) * 3.6 - Math.max(0, ph - 0.45) * 1.5);  // bounces back off the gate
      }
      m.position.y = 0.32 + (it.pick ? 0 : Math.max(0, ph - 0.45) * 0.4);
    });
  });
  return (
    <group position={[0, FLOOR, 0]}>
      {shown.map((l, i) => (
        <group key={l.sector} position={[0, 0, laneZ(i)]}>
          <mesh position={[0, 0.06, 0]}><boxGeometry args={[3.8, 0.08, 0.36]} />{metal(C.iron, 0.6, 0.3)}</mesh>
          <mesh position={[0, 0.7, -0.22]}><boxGeometry args={[0.12, 1.2, 0.06]} />{metal(C.brass)}</mesh>
          <mesh position={[0, 0.7, 0.22]}><boxGeometry args={[0.12, 1.2, 0.06]} />{metal(C.brass)}</mesh>
          {Array.from({ length: l.slots }, (_, k) => (
            <mesh key={k} position={[0.02, 1.2 - k * 0.16, 0]}><boxGeometry args={[0.05, 0.08, 0.36]} />
              <meshStandardMaterial color={k < l.picks.length ? STATUS_COLOR.ok : C.steel} emissive={k < l.picks.length ? STATUS_COLOR.ok : "#000"} emissiveIntensity={0.5} /></mesh>
          ))}
        </group>
      ))}
      {items.map((it, k) => (
        <mesh key={it.key} ref={(m) => { tokens.current[k] = m; }} position={[-1.8, 0.32, laneZ(it.lane)]}>
          <boxGeometry args={[0.2, 0.2, 0.2]} />
          <meshStandardMaterial color={it.pick ? C.blue : C.steel} metalness={0.3} roughness={0.4} emissive={it.pick ? C.blue : "#000"} emissiveIntensity={it.pick ? 0.4 : 0} />
        </mesh>
      ))}
      {!shown.length && <mesh position={[0, 0.7, 0]}><boxGeometry args={[0.12, 1.2, 1.4]} />{metal(C.iron)}</mesh>}
    </group>
  );
}

// 11 ------------------------------------------------------------------------------------------------
export function VaultPart({ p, scale, trades }: { p: PartProps; scale: MachineState["scale"]; trades: MachineState["trades"] }) {
  const beam = useRef<THREE.Group>(null);
  const left = useRef<THREE.Group>(null);
  const right = useRef<THREE.Group>(null);
  const coins = useRef<(THREE.Mesh | null)[]>([]);
  const door = useRef<THREE.Group>(null);
  const tilt = scaleTilt(scale);
  const total = scale ? scale.cash + scale.holdings : 1;
  const cashStack = scale ? Math.max(1, Math.round((scale.cash / total) * 12)) : 0;
  const holdStack = scale ? Math.max(scale.holdings > 0 ? 1 : 0, Math.round((scale.holdings / total) * 12)) : 0;
  const L = 1.1;
  useFrame((s) => {
    const t = partTime(s.clock.elapsedTime, p, 99);
    const a = p.reduced ? tilt : tilt * Math.min(1, t * 0.4) + Math.sin(t * 1.3) * 0.015 * (p.running ? 1 : 0);
    if (beam.current) beam.current.rotation.z = -a;
    if (left.current) left.current.position.set(-L * Math.cos(a), 2.1 + L * Math.sin(a), 0);
    if (right.current) right.current.position.set(L * Math.cos(a), 2.1 - L * Math.sin(a), 0);
    if (door.current) door.current.rotation.z = t * 0.4;
    trades.forEach((tr, k) => {
      const m = coins.current[k];
      if (!m) return;
      const ph = (t * 0.3 + k * 0.23) % 1;
      const from = tr.side === "buy" ? -L : L, to = -from;
      m.position.set(from + (to - from) * ph, 1.8 + Math.sin(ph * Math.PI) * 0.9, 0.05);
      m.visible = p.running && !p.reduced;
    });
  });
  return (
    <group position={[0, FLOOR, 0]}>
      {/* vault */}
      <mesh position={[0, 1.05, -1.1]}><boxGeometry args={[3.6, 2.1, 1.1]} />{metal(C.iron, 0.55, 0.6)}</mesh>
      <group ref={door} position={[0, 1.05, -0.53]}>
        <mesh rotation={[Math.PI / 2, 0, 0]}><cylinderGeometry args={[0.75, 0.75, 0.1, 40]} />{metal(C.steel, 0.25)}</mesh>
        {[0, 1, 2].map((k) => <mesh key={k} rotation={[0, 0, (k * Math.PI) / 3]} position={[0, 0, 0.07]}><boxGeometry args={[1.1, 0.08, 0.05]} />{metal(C.brass)}</mesh>)}
      </group>
      {/* scales */}
      <mesh position={[0, 1.05, 0.3]}><cylinderGeometry args={[0.07, 0.12, 2.1, 12]} />{metal(C.brass, 0.3)}</mesh>
      <group position={[0, 0, 0.3]}>
        <group ref={beam} position={[0, 2.1, 0]}>
          <mesh><boxGeometry args={[L * 2 + 0.2, 0.07, 0.07]} />{metal(C.brass, 0.3)}</mesh>
        </group>
        {[{ ref: left, n: cashStack, color: C.brass, label: "cash" }, { ref: right, n: holdStack, color: C.blue, label: "holdings" }].map((pan) => (
          <group key={pan.label} ref={pan.ref}>
            <mesh position={[0, -0.45, 0]}><cylinderGeometry args={[0.01, 0.01, 0.9, 4]} />{metal(C.iron)}</mesh>
            <mesh position={[0, -0.9, 0]}><cylinderGeometry args={[0.42, 0.32, 0.06, 24]} />{metal(C.brass, 0.3)}</mesh>
            {Array.from({ length: pan.n }, (_, k) => (
              <mesh key={k} position={[0, -0.84 + k * 0.05, 0]}><cylinderGeometry args={[0.2, 0.2, 0.045, 18]} />
                <meshStandardMaterial color={pan.color} metalness={0.7} roughness={0.3} /></mesh>
            ))}
          </group>
        ))}
        {trades.map((tr, k) => (
          <mesh key={`${tr.ticker}-${k}`} ref={(m) => { coins.current[k] = m; }} rotation={[Math.PI / 2, 0, 0]}>
            <cylinderGeometry args={[0.13, 0.13, 0.04, 16]} />
            <meshStandardMaterial color={tr.side === "buy" ? C.blue : C.brass} emissive={tr.side === "buy" ? C.blue : C.brass} emissiveIntensity={0.35} />
          </mesh>
        ))}
      </group>
    </group>
  );
}

// 12 ------------------------------------------------------------------------------------------------
export function ChutePart({ p, sent }: { p: PartProps; sent: boolean | null }) {
  const env = useRef<THREE.Mesh>(null);
  useFrame((s) => {
    const t = partTime(s.clock.elapsedTime, p, 0.3);
    if (env.current) {
      const ph = (t * 0.4) % 1;
      env.current.position.set(-0.7 + ph * 1.4, 2.2 - ph * 1.5, 0);
      env.current.visible = sent === true;
    }
  });
  return (
    <group position={[0, FLOOR, 0]}>
      <mesh position={[0, 1.45, 0]} rotation={[0, 0, -0.82]}><boxGeometry args={[2.3, 0.08, 0.9]} />{metal(C.steel, 0.4)}</mesh>
      <mesh position={[0, 1.45, -0.45]} rotation={[0, 0, -0.82]}><boxGeometry args={[2.3, 0.3, 0.05]} />{metal(C.steel, 0.4)}</mesh>
      <mesh position={[0.85, 0.35, 0]}><boxGeometry args={[0.8, 0.7, 1]} /><meshStandardMaterial color={STATUS_COLOR.fail} metalness={0.3} roughness={0.5} /></mesh>
      <mesh ref={env} rotation={[0, 0, -0.82]}><boxGeometry args={[0.5, 0.04, 0.34]} /><meshStandardMaterial color={C.paper} roughness={0.9} /></mesh>
    </group>
  );
}
