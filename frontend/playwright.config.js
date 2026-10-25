// @ts-check
import fs from "node:fs";
import { defineConfig, devices } from "@playwright/test";

// Set PLAYWRIGHT_BROWSERS_PATH when running browser tests.
const browsers = process.env.PLAYWRIGHT_BROWSERS_PATH || "";
if (browsers && fs.existsSync(browsers)) {
  process.env.PLAYWRIGHT_BROWSERS_PATH = browsers;
}

const participant = process.env.INVESTIGATORS_E2E === "1";

export default defineConfig({
  testDir: "./e2e",
  testIgnore: participant ? [] : ["**/participant.spec.js"],
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  timeout: participant ? 120_000 : 30_000,
  reporter: "list",
  // The API and Vite are started by global setup. Playwright's webServer starts
  // before that hook, so it cannot wait on the port file.
  globalSetup: participant ? "./e2e/global-setup.js" : undefined,
  globalTeardown: participant ? "./e2e/global-teardown.js" : undefined,
  // 4180 is the cut proxy in front of Vite. Closing that socket is what the
  // event-stream scenario uses to kill a live connection.
  use: participant ? { baseURL: "http://127.0.0.1:4180" } : undefined,
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
