/**
 * M13-H3a — global setup guard.
 *
 * Probes the compose stack before spending any browser time. Deployed
 * topology (deploy/docker-compose.yml): the frontend answers on :3000 and the
 * backend services answer THEIR OWN /health endpoints on their host ports
 * (identity :8001 … review :8008). There is no same-origin /api/v1 proxy on
 * the frontend — section7/src/lib/api.ts calls the service ports directly
 * (CORS allow-lists http://localhost:3000). Candidate health paths, in order:
 *   1. /api/v1/health      — gateway-style deployments (Caddy profile, future)
 *   2. /health             — the compose services' actual liveness route
 * against E2E_API_URL (default http://localhost:8001, the identity service the
 * journey logs in through). The frontend origin itself must also answer.
 *
 * When the stack is down the suite is SKIPPED with exit code 246 — CI jobs
 * that don't start compose stay green, and `make e2e` (H3b's target) only
 * runs us where a stack actually exists. Exit code rationale: Playwright
 * reserves 0/1 for test outcomes and 130/143 for interrupts; 246 is unused,
 * so scripts can tell "skipped: no stack" from "tests failed".
 */
import { exit } from "node:process";

const STACK_DOWN_EXIT_CODE = 246;
const HEALTH_CANDIDATE_PATHS = ["/api/v1/health", "/health"];
const ATTEMPTS = Number(process.env.E2E_HEALTH_ATTEMPTS ?? "5");

async function jsonOk(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(4_000) });
    if (!res.ok) return false;
    const body = (await res.json().catch(() => null)) as { ok?: unknown } | null;
    return body === null || body.ok === true; // 2xx wins; body shape is informational
  } catch {
    return false;
  }
}

async function stackUp(): Promise<boolean> {
  const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3000";
  const apiUrl = process.env.E2E_API_URL ?? process.env.E2E_IDENTITY_URL ?? "http://localhost:8001";

  // Frontend must answer (ANY response — even a 404 — proves the server is up).
  try {
    await fetch(baseURL, { signal: AbortSignal.timeout(4_000) });
  } catch {
    return false;
  }
  // Backend must answer on one of the candidate health paths.
  for (const path of HEALTH_CANDIDATE_PATHS) {
    if (await jsonOk(`${apiUrl}${path}`)) return true;
  }
  return false;
}

export default async function globalSetup(): Promise<void> {
  // E2E_SKIP_STACK_CHECK=1 opts out (e.g. probing an authenticated-only env).
  if (process.env.E2E_SKIP_STACK_CHECK === "1") return;

  const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3000";
  for (let attempt = 1; attempt <= ATTEMPTS; attempt += 1) {
    if (await stackUp()) return;
    if (attempt < ATTEMPTS) await new Promise((r) => setTimeout(r, 2_000));
  }

  console.warn(
    `\n[e2e] SKIP: compose stack is not reachable (frontend ${baseURL}, api ` +
      `${process.env.E2E_API_URL ?? process.env.E2E_IDENTITY_URL ?? "http://localhost:8001"}).\n` +
      `[e2e] The hero-journey suite needs the full stack.\n` +
      `[e2e] Start it with:\n` +
      `[e2e]   docker compose -f deploy/docker-compose.yml up -d --build --wait\n` +
      `[e2e] or point E2E_BASE_URL / E2E_API_URL at a running environment.\n` +
      `[e2e] Exiting ${STACK_DOWN_EXIT_CODE} so CI can distinguish skip-from-failure.\n`,
  );
  exit(STACK_DOWN_EXIT_CODE);
}
