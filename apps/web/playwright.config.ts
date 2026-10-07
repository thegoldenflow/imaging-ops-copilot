import { defineConfig } from "@playwright/test";

// Set PW_CHROMIUM_PATH to use a preinstalled Chromium (e.g. in cloud sessions).
const executablePath = process.env.PW_CHROMIUM_PATH || undefined;
const apiPort = process.env.PW_API_PORT || "8000";
const apiUrl = `http://127.0.0.1:${apiPort}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5173",
    launchOptions: { executablePath },
    viewport: { width: 1360, height: 900 },
  },
  webServer: [
    {
      command: `cd ../api && uv run uvicorn app.main:app --host 127.0.0.1 --port ${apiPort}`,
      url: `${apiUrl}/api/health`,
      reuseExistingServer: true,
      timeout: 60_000,
    },
    {
      command: "npx vite --host 127.0.0.1 --port 5173",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: true,
    },
  ],
});
