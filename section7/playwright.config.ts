/**
 * M13-H3a (H-014) — Playwright config for the E2E hero-journey suite.
 *
 * Target: the compose stack's frontend at http://localhost:3000 (override via
 * E2E_BASE_URL — deploy/docker-compose.yml maps `frontend` to host :3000).
 *
 * Guard: e2e/global-setup.ts probes GET {baseURL}/api/v1/health (the identity
 * service's liveness endpoint, which the Next dev proxy exposes under /api/v1)
 * and SKIPS the whole suite with exit code 246 when the stack is down, so CI
 * stays green on jobs that don't start compose (the e2e job starts it — recipe
 * in e2e/README.md, owned by H3b's ci.yml wiring).
 *
 * Naming law: specs match `*.e2e.ts` (NOT `*.spec.ts`) so vitest's default
 * include glob never picks them up — one repo, two runners, zero config
 * edits outside this lane's owned paths.
 */
import { defineConfig } from "@playwright/test";

const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.e2e\.ts/,
  timeout: Number(process.env.E2E_TEST_TIMEOUT_MS ?? "90000"),
  expect: { timeout: 15_000 },
  // The hero journey is a stateful sequence over one browser session —
  // parallelizing it would race its own localStorage/backend rows.
  fullyParallel: false,
  workers: 1,
  retries: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never", outputFolder: "../playwright-report" }]] : "list",
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL,
    trace: "on-first-retry",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
