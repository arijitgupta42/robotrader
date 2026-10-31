/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served by GitHub Pages at https://arijitgupta42.github.io/robotrader/
export default defineConfig({
  base: "/robotrader/",
  plugins: [react()],
  // The 3D machine (three.js) is one lazily loaded chunk of about 240 kB gzipped; the page itself does not wait for it.
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1000 },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
