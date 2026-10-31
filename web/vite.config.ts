/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served by GitHub Pages at https://arijitgupta42.github.io/robotrader/
export default defineConfig({
  base: "/robotrader/",
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
