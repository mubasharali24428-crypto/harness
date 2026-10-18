// W1-C2 (C-005 UI half) — plan-gate API client. Implements the S7 side of the
// PAIR CONTRACT W1-C1↔W1-C2 verbatim:
//
//   POST {ORCH}/api/v1/orchestrator/plans            {goal}  → 201 {planId, plan}
//   POST {ORCH}/api/v1/orchestrator/plans/{id}/approve       → 200 {runId}
//   POST {ORCH}/api/v1/orchestrator/plans/{id}/reject        → 202
//
// DTOs mirror W1-C1's response models FIELD-BY-FIELD including nullability.
// No other endpoints are called — "no invented endpoints" is a hard rule of
// this lane. Bearer/error conventions identical to src/lib/api.ts; base URL
// re-declared locally because src/lib/api.ts is outside this lane's owned
// paths (same pattern costApi.ts used).
import { ApiError, parseApiError } from "@/lib/api";
import type { AgentPlan } from "@contracts/plan";

export const PLAN_GATE_BASE =
  process.env.NEXT_PUBLIC_ORCHESTRATOR_BASE_URL ?? "http://localhost:8004";

const PLANS_ROOT = "/api/v1/orchestrator/plans";

/** Wire shape of `POST /plans` → 201. `plan` is the frozen contracts AgentPlan
 *  (goal/expectedResult required; steps, filesLikelyToChange, tests, risks,
 *  forbiddenPaths, estimatedCostUsd optional on the TS mirror). */
export interface ProposePlanResponse {
  planId: string;
  plan: AgentPlan;
}

/** Wire shape of `POST /plans/{id}/approve` → 200. The orchestrator mints and
 *  binds the run server-side; the client never invents a run id. */
export interface ApprovePlanResponse {
  runId: string;
}

/**
 * Wire shape of `POST /plans/{id}/reject` → 202 (accepted, audited via W1-A2).
 * The pair contract defines NO body for this response — typed honestly as
 * unknown so callers can't read fields that were never contracted.
 */
export type RejectPlanResponse = unknown;

export function proposePlanUrl(): string {
  return `${PLAN_GATE_BASE}${PLANS_ROOT}`;
}
export function approvePlanUrl(planId: string): string {
  return `${PLAN_GATE_BASE}${PLANS_ROOT}/${encodeURIComponent(planId)}/approve`;
}
export function rejectPlanUrl(planId: string): string {
  return `${PLAN_GATE_BASE}${PLANS_ROOT}/${encodeURIComponent(planId)}/reject`;
}

async function request<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
      ...(init?.headers ?? {}),
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

/** Pair-contract step 1: propose. Body is exactly `{goal}` per contract unless
 *  a projectId is given (B1): then `{goal, projectId}` so the server binds the
 *  run to that project (foreign/unknown ids are a 404). Omitting it keeps the
 *  legacy body; the server then binds the team's only project, if exactly one. */
export async function proposePlan(
  goal: string,
  token: string,
  projectId?: string | null,
): Promise<ProposePlanResponse> {
  return request<ProposePlanResponse>(proposePlanUrl(), token, {
    method: "POST",
    body: JSON.stringify(projectId ? { goal, projectId } : { goal }),
  });
}

/** Pair-contract step 2: approve → returns the server-minted run binding. */
export async function approvePlan(planId: string, token: string): Promise<ApprovePlanResponse> {
  return request<ApprovePlanResponse>(approvePlanUrl(planId), token, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

/** Pair-contract step 3 (terminal): reject → 202 accepted, event + audit row
 *  persisted by Section 4 / audited via W1-A2. */
export async function rejectPlan(planId: string, token: string): Promise<RejectPlanResponse> {
  return request<RejectPlanResponse>(rejectPlanUrl(planId), token, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

/** Wire shape of `POST /runs/{runId}/start` → 200 (Section 4's existing route,
 *  section4/src/harness_orchestrator/api.py::start_execution). Owned here
 *  because src/lib/api.ts is outside this lane's paths and S-005 asks for a
 *  plan-gate-scoped startRun method. */
export interface StartRunResponse {
  runId: string;
  status: string;
}

/** Gate step 3: begin execution after approval. */
export function startRun(runId: string, token: string): Promise<StartRunResponse> {
  return request<StartRunResponse>(
    `${PLAN_GATE_BASE}/api/v1/orchestrator/runs/${encodeURIComponent(runId)}/start`,
    token,
    { method: "POST", body: JSON.stringify({}) },
  );
}

// ---------------------------------------------------------------------------
// Pure state→action mapping (the card renders from these, not from ad-hoc ifs)
// ---------------------------------------------------------------------------

/** The plan-gate states the card distinguishes, derived from RunStatus.state
 *  (Section 4's REAL AgentState vocabulary, mirrored in @contracts/api). */
export type PlanGatePhase =
  | "no-plan" // idle: nothing proposed yet → Propose enabled
  | "awaiting-approval" // plan exists, not approved → Approve / Reject enabled
  | "ready-to-start" // approved, not executing → Start enabled
  | "in-flight" // executing or later lifecycle states → gate closed
  | "done"; // done terminal → gate closed permanently

/** Pure classifier: RunStatus.state (plus the snapshot's planApproved flag and
 *  whether a planId exists at all) → PlanGatePhase. Unknown server states
 *  degrade to "in-flight" honestly rather than guessing at an action. */
export function classifyGatePhase(
  state: string | null | undefined,
  planApproved: boolean | undefined,
  hasPlan = false,
): PlanGatePhase {
  switch (state) {
    case null:
    case undefined:
    case "":
      return "no-plan";
    case "idle":
      // Fresh run (no plan proposed yet): the gate opens with Propose. After a
      // rejection S4 returns the run to idle WITH the plan still bound — then
      // the operator decides approve/reject again.
      if (planApproved) return "ready-to-start";
      return hasPlan ? "awaiting-approval" : "no-plan";
    case "planning":
      return "no-plan";
    case "awaiting_plan_approval":
      return planApproved ? "ready-to-start" : "awaiting-approval";
    case "executing":
    case "paused":
    case "awaiting_done_signal":
    case "reviewing":
    // pr_opened/blocked keep the gate closed but still show Approve as a
    // no-op-disabled affordance below — classified in-flight either way.
    case "pr_opened":
    case "blocked":
      return "in-flight";
    case "done":
      return "done";
    default:
      // Unknown future state from the server: render honestly, enable nothing.
      return "in-flight";
  }
}

/** Role gates per W1-C1's brief: admin/engineer act on plans; viewer none.
 *  guest is likewise never an approver (contracts/api-conventions.md). */
export type GateRole = "admin" | "engineer" | "viewer" | "guest" | string;

export function roleCanAct(role: GateRole | null | undefined): boolean {
  return role === "admin" || role === "engineer";
}

/** Pure: which buttons light up for a phase × role × busy combination. */
export interface GateActions {
  propose: boolean;
  approve: boolean;
  reject: boolean;
  start: boolean;
}

export function gateActionsFor(
  phase: PlanGatePhase,
  role: GateRole | null | undefined,
  busy: boolean,
): GateActions {
  const canAct = roleCanAct(role) && !busy;
  switch (phase) {
    case "no-plan":
      return { propose: canAct, approve: false, reject: false, start: false };
    case "awaiting-approval":
      return { propose: false, approve: canAct, reject: canAct, start: false };
    case "ready-to-start":
      return { propose: false, approve: false, reject: false, start: canAct };
    case "in-flight":
    case "done":
      return { propose: false, approve: false, reject: false, start: false };
  }
}

/** Pure: short honest caption under each phase. */
export function gateCaption(phase: PlanGatePhase): string {
  switch (phase) {
    case "no-plan":
      return "No plan yet — propose one to open the gate.";
    case "awaiting-approval":
      return "Plan awaiting approval.";
    case "ready-to-start":
      return "Plan approved — start when ready.";
    case "in-flight":
      return "Run in flight — the plan gate is closed.";
    case "done":
      return "Run complete.";
  }
}
