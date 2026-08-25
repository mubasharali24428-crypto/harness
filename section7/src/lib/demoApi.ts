// M19-N4 (C10) — canned-demo client. One call, zero provider keys:
//
//   POST {ORCH}/api/v1/orchestrator/demo/seed → 201 {projectId, runId, ...}
//
// DTO mirrors Section 4's DemoSeedResponse FIELD-BY-FIELD (including the
// explicit replay flag — this module refuses to hide what the seed is).
// Bearer/error conventions identical to runsApi.ts; base re-declared locally
// because src/lib/api.ts is outside this lane's owned paths.
import { ApiError, parseApiError } from "@/lib/api";

export const DEMO_BASE =
  process.env.NEXT_PUBLIC_ORCHESTRATOR_BASE_URL ?? "http://localhost:8004";

/** Wire shape of POST /demo/seed → 201. `replay` is ALWAYS true on success:
 *  the seeded run is recorded truth, and the client keeps saying so. */
export interface DemoSeedResponse {
  projectId: string;
  projectName: string;
  repoFullName: string | null;
  runId: string;
  state: string;
  planId: string;
  prId: string;
  prUrl: string | null;
  costUsd: number | null;
  /** Server-asserted replay marker; typed non-optional so callers can trust it. */
  replay: true;
}

export function demoSeedUrl(): string {
  return `${DEMO_BASE}/api/v1/orchestrator/demo/seed`;
}

async function request<T>(path: string, token: string): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
    },
  });
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!res.ok) {
    const { message, code } = parseApiError(res.status, body);
    throw new ApiError(message, code, res.status);
  }
  return body as T;
}

/** POST /demo/seed — materializes the caller's team's demo graph.
 *  Idempotent server-side: repeat calls return the same project/run ids. */
export function seedDemo(token: string): Promise<DemoSeedResponse> {
  return request<DemoSeedResponse>(demoSeedUrl(), token);
}

/** Pure: relative href of the wow-moment page for a seeded run. */
export function demoRunHref(runId: string, projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}`;
}
