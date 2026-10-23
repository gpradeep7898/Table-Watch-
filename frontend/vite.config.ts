/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.TABLEWATCH_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: api, changeOrigin: false },
      "/ws": { target: api.replace("http", "ws"), ws: true },
    },
  },
  preview: { host: "127.0.0.1", port: 4173 },
  build: { chunkSizeWarningLimit: 1200 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
