// A4-S1: real-browser review suite (real Chromium <-> real developer-workflow service).
// Separate from the compose-bound hero-journey config; needs only python + the service venv.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e-review",
  testMatch: /.*\.e2e\.ts/,
  timeout: 60_000,
  workers: 1,
  reporter: "list",
  use: { browserName: "chromium", viewport: { width: 1000, height: 900 } },
});
