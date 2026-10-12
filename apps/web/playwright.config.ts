import { defineConfig } from "@playwright/test";

// Set PW_CHROMIUM_PATH to use a preinstalled Chromium (e.g. in cloud sessions).
const executablePath = process.env.PW_CHROMIUM_PATH || undefined;
const apiPort = process.env.PW_API_PORT || "8000";
const apiUrl = `http://127.0.0.1:${apiPort}`;
// PW_WEB_PORT: another Vite port when a second session runs its own web server on 5173 at the same time.
const webPort = process.env.PW_WEB_PORT || "5173";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
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
      command: `npx vite --host 127.0.0.1 --port ${webPort} --strictPort`,
      url: `http://127.0.0.1:${webPort}`,
      reuseExistingServer: true,
      // Vite proxies /api to the API above unless VITE_API_PROXY_TARGET says otherwise.
      env: { VITE_API_PROXY_TARGET: process.env.VITE_API_PROXY_TARGET ?? apiUrl },
    },
  ],
});
