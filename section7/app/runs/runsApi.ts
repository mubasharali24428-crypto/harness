// M12-E3b — runs-index API client, extended for the H-009 click-through.
// Lives next to the page; consumes contract M10-C2 (`GET
// /api/v1/orchestrator/runs`, bearer auth) served by Section 4's orchestrator,
// with the same bearer/error conventions as src/lib/api.ts. The backend side
// landed separately — this client codes against the contract, not against any
// particular implementation.

import { ApiError, ORCHESTRATOR_BASE, parseApiError } from "@/lib/api";

/** Pure: the detail route a run row deep-links to (H-009). Centralized so the
 *  table and any future consumer share one URL shape: /projects/{projectId}/runs/{id}. */
export function runDetailHref(run: Pick<RunDto, "id" | "projectId">): string {
  return `/projects/${run.projectId}/runs/${run.id}`;
}

/** Orchestrator run statuses — mirrors Section 4's REAL AgentState enum
 *  (section4/src/harness_orchestrator/state_machine.py), as also mirrored in
 *  contracts/ts/src/api.ts RunState.
 *  Unknown values coming back from the server are rendered honestly as-is. */
export const RUN_STATUSES = [
  "idle",
  "planning",
  "awaiting_plan_approval",
  "executing",
  "paused",
  "awaiting_done_signal",
  "reviewing",
  "blocked",
  "pr_opened",
  "done",
] as const;

export type RunStatusValue = (typeof RUN_STATUSES)[number];

/** Contract M10-C2 run item — camelCase per contracts/api-conventions.md. */
export interface RunDto {
  id: string;
  projectId: string;
  status: string;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
}

/** Contract M10-C2 list response — paging fields come back FROM the server
 *  and drive the pager (never assume client-side totals). */
export interface RunsPageDto {
  items: RunDto[];
  total: number;
  limit: number;
  offset: number;
}

export interface RunsQuery {
  projectId?: string | null;
  status?: RunStatusValue | null;
  /** Contract cap: limit ≤ 100, server default 25. Clamped client-side too. */
  limit?: number;
  offset?: number;
}

export const RUNS_DEFAULT_LIMIT = 25;
export const RUNS_MAX_LIMIT = 100;

function normalizeLimit(limit: number): number {
  if (!Number.isFinite(limit)) return RUNS_DEFAULT_LIMIT;
  return Math.min(RUNS_MAX_LIMIT, Math.max(1, Math.floor(limit)));
}

/** Pure: builds the M10-C2 query string. Omits empty filters and offset=0 so
 *  the URL stays canonical; trims projectId; clamps limit to [1,100]. */
export function buildRunsQueryString(query: RunsQuery): string {
  const params = new URLSearchParams();
  const projectId = query.projectId?.trim();
  if (projectId) params.set("projectId", projectId);
  if (query.status) params.set("status", query.status);
  if (typeof query.limit === "number" && Number.isFinite(query.limit)) {
    params.set("limit", String(normalizeLimit(query.limit)));
  }
  if (
    typeof query.offset === "number" &&
    Number.isFinite(query.offset) &&
    query.offset > 0
  ) {
    params.set("offset", String(Math.floor(query.offset)));
  }
  return params.toString();
}

async function request<T>(path: string, token: string): Promise<T> {
  const res = await fetch(`${ORCHESTRATOR_BASE}${path}`, {
    headers: { authorization: `Bearer ${token}` },
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

/** GET /api/v1/orchestrator/runs — M10-C2. Team scoping happens server-side
 *  via the bearer token; the client only narrows with the given filters. */
export async function listRuns(
  query: RunsQuery,
  token: string,
): Promise<RunsPageDto> {
  const qs = buildRunsQueryString(query);
  const data = await request<Partial<RunsPageDto>>(
    `/api/v1/orchestrator/runs${qs ? `?${qs}` : ""}`,
    token,
  );
  // Light shape guard: a drifted/empty body renders as an honest empty page,
  // never as a crash on data.items.map.
  return {
    items: Array.isArray(data.items) ? data.items : [],
    total: typeof data.total === "number" ? data.total : 0,
    limit:
      typeof data.limit === "number" && data.limit > 0
        ? data.limit
        : RUNS_DEFAULT_LIMIT,
    offset: typeof data.offset === "number" && data.offset >= 0 ? data.offset : 0,
  };
}

/** Pure: relative time ("4m ago") from an ISO timestamp against `now`.
 *  Null/unparseable input renders as "—" — never a fabricated time. */
export function formatRelativeTime(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "—";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "—";
  const diffMs = now - then;
  const seconds = Math.floor(Math.abs(diffMs) / 1000);
  let span: string;
  if (seconds < 5) return "just now";
  if (seconds < 60) span = `${seconds}s`;
  else if (seconds < 3600) span = `${Math.floor(seconds / 60)}m`;
  else if (seconds < 86400) span = `${Math.floor(seconds / 3600)}h`;
  else span = `${Math.floor(seconds / 86400)}d`;
  return diffMs < 0 ? `in ${span}` : `${span} ago`;
}

/** Pure: short form of a run id for the table (full id stays in title attr). */
export function shortId(id: string): string {
  return id.length > 12 ? id.slice(0, 8) : id;
}
