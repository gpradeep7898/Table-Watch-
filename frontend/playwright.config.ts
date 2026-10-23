import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

// Uses a throwaway database and a paused simulator so tests are deterministic.
const py = existsSync("../backend/.venv/bin/python") ? "../backend/.venv/bin/python" : "python3";
const chromium = process.env.TABLEWATCH_CHROMIUM;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5174",
    viewport: { width: 1440, height: 1000 },
    launchOptions: chromium ? { executablePath: chromium } : {},
  },
  webServer: [
    {
      command: `rm -f /tmp/tablewatch-e2e.db* && TABLEWATCH_ALLOWED_ORIGINS=http://127.0.0.1:5174 TABLEWATCH_DB=/tmp/tablewatch-e2e.db TABLEWATCH_AUTOPLAY=0 TABLEWATCH_PORT=8765 ${py} -m tablewatch.cli serve`,
      cwd: "../backend",
      url: "http://127.0.0.1:8765/api/health",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "TABLEWATCH_API=http://127.0.0.1:8765 npx vite --host 127.0.0.1 --port 5174 --strictPort",
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
