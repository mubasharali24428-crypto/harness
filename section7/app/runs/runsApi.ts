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
 *  M13-G5b: the implementation moved to the shared src/lib/format.ts so every
 *  page renders times identically; re-exported here so this module's callers
 *  keep one import path. */
export { formatRelativeTime } from "@/lib/format";

/** Pure: short form of a run id for the table (full id stays in title attr). */
export function shortId(id: string): string {
  return id.length > 12 ? id.slice(0, 8) : id;
}

// ---------------------------------------------------------------------------
// M14-W4 F3b (W4-C2) — single-run AGGREGATE read for the run detail page.
// Consumes F3a's enriched `GET /api/v1/orchestrator/runs/{runId}`
// (Section 4 orchestrator). Pair-contract shape
// {runId, state, costUsd?, prId?, url?, steps?} with EXPLICIT nullability:
// every enriched field arrives present-but-null until its source exists, and
// this client preserves null-vs-zero so the page can degrade honestly (never
// a fabricated $0 or an invented PR link). The server side lands concurrently
// in F3a's lane — this client is UNVERIFIED-until-server by design; ATLAS
// diffs the pair at the gate.

/** One bus-event shadow inside the aggregate's steps summary (last ≤100). */
export interface RunStepDto {
  seq: number;
  /** Raw event type from agent_events (contracts EventType vocabulary). */
  type: string;
  /** ISO timestamp when the server had one; null stays null — never faked. */
  ts: string | null;
}

/** W4-C2 run aggregate wire shape (camelCase per api-conventions.md). */
export interface RunAggregateDto {
  runId: string;
  state: string;
  planId: string | null;
  planApproved: boolean;
  isHumanEditing: boolean;
  executing: boolean;
  engineMode: string | null;
  /** SUM(cost_ledger.cost_usd) for this run at NUMERIC-exact scale-6 money,
   *  or null while metering has recorded nothing — NEVER defaulted to 0. */
  costUsd: number | null;
  /** agent_runs.pr_id once Section 2 binds a PR to this run; null before. */
  prId: string | null;
  /** The bound PR's url; independently nullable (prId can exist without it). */
  url: string | null;
  /** Last ≤100 events, wire order OLDEST→newest; the UI flips newest-first. */
  steps: RunStepDto[];
}

/** Client cap mirroring F3a's _AGGREGATE_STEPS_MAX: even if a future server
 *  lifts its cap, the timeline still renders at most this many events. */
export const RUN_AGGREGATE_STEPS_MAX = 100;

/** Pure: "$12.500000" from the NUMERIC-exact scale-6 wire number. Non-finite
 *  garbage degrades to "—" — never a fake "$0.000000". A genuine server-sent
 *  0 formats honestly as "$0.000000". */
export function formatCostUsdExact(costUsd: number): string {
  return Number.isFinite(costUsd) ? `$${costUsd.toFixed(6)}` : "—";
}

/** Run state → theme-var pill class over the REAL AgentState vocabulary —
 *  the SAME color mapping as the runs-index table (app/runs/page.tsx):
 *  active work yellow, human/waiting gates blue, success green, failure red.
 *  Unknown values fall through honestly to the uncolored ".pill" base.
 *  Local copy: that page's map is file-private and other lanes own the file. */
const STATE_PILL_CLASS: Record<string, string> = {
  idle: "pill blue",
  planning: "pill yellow",
  awaiting_plan_approval: "pill blue",
  executing: "pill yellow",
  paused: "pill yellow",
  awaiting_done_signal: "pill blue",
  reviewing: "pill yellow",
  blocked: "pill red",
  pr_opened: "pill blue",
  done: "pill green",
};

/** Pure: pill class for a run state; unknown states stay uncolored. */
export function statePillClass(state: string): string {
  return STATE_PILL_CLASS[state] ?? "pill";
}

/** Human labels for the event-type vocabulary (contracts EventType). The raw
 *  wire type stays reachable in each row's title attribute; unknown types are
 *  humanized (underscores → spaces), never dropped. */
const STEP_LABELS: Record<string, string> = {
  plan_proposed: "Plan proposed",
  plan_approved: "Plan approved",
  execution_started: "Execution started",
  paused: "Paused",
  resumed: "Resumed",
  blocked_awaiting_clarification: "Blocked — awaiting clarification",
  done_signal_received: "Done signal received",
  review_passed: "Review passed",
  review_failed: "Review failed",
  pr_opened: "PR opened",
  terminal_output: "Terminal output",
  diff_progress: "Diff progress",
  engine_completed: "Engine completed",
  engine_failed: "Engine failed",
};

/** Pure: human label for a raw event type. */
export function stepLabel(type: string): string {
  const known = STEP_LABELS[type];
  if (known) return known;
  return type.replace(/_/g, " ").trim() || "unknown event";
}

/** Pure: flip wire order (oldest→newest) into display order (newest-first),
 *  defensively capped at `cap`. Non-mutating — the input array is untouched. */
export function stepsNewestFirst(
  steps: RunStepDto[],
  cap: number = RUN_AGGREGATE_STEPS_MAX,
): RunStepDto[] {
  return [...steps].reverse().slice(0, Math.max(0, cap));
}

function asStringOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/** GET /api/v1/orchestrator/runs/{runId} — W4-C2 aggregate (F3a server side).
 *  Per-field light shape guard: a drifted/partial body degrades each field to
 *  its explicit null default instead of crashing the page on data.steps.map. */
export async function getRunAggregate(
  runId: string,
  token: string,
): Promise<RunAggregateDto> {
  const data = await request<Partial<RunAggregateDto>>(
    `/api/v1/orchestrator/runs/${encodeURIComponent(runId)}`,
    token,
  );
  const steps: RunStepDto[] = [];
  if (Array.isArray(data.steps)) {
    for (const raw of data.steps) {
      if (!raw || typeof raw !== "object") continue;
      const rec = raw as unknown as Record<string, unknown>;
      steps.push({
        seq:
          typeof rec.seq === "number" && Number.isFinite(rec.seq)
            ? rec.seq
            : Number.NaN,
        type: typeof rec.type === "string" ? rec.type : "unknown",
        ts: asStringOrNull(rec.ts),
      });
      if (steps.length >= RUN_AGGREGATE_STEPS_MAX) break;
    }
  }
  return {
    runId: typeof data.runId === "string" ? data.runId : runId,
    state: typeof data.state === "string" ? data.state : "unknown",
    planId: asStringOrNull(data.planId),
    planApproved: data.planApproved === true,
    isHumanEditing: data.isHumanEditing === true,
    executing: data.executing === true,
    engineMode: asStringOrNull(data.engineMode),
    costUsd:
      typeof data.costUsd === "number" && Number.isFinite(data.costUsd)
        ? data.costUsd
        : null,
    prId: asStringOrNull(data.prId),
    url: asStringOrNull(data.url),
    steps,
  };
}
