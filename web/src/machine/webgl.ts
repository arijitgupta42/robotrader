/** Whether this browser can draw the 3D machine (kept out of Machine.tsx so the page can ask without loading three.js). */
export function webglAvailable(): boolean {
  try {
    const c = document.createElement("canvas");
    return Boolean(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}
