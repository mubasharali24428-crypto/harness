// Mirrors contracts/src/harness_contracts/api.py + api-conventions.md.

export const API_PREFIX = "/api/v1";

export interface ApiErrorBody {
  error: { code: string; message: string };
}

export interface WsEnvelope<T = unknown> {
  type: string;
  payload: T;
  ts: string;
  seq: number;
}

// Mirrors section4's ACTUAL AgentState (harness_orchestrator/state_machine.py) as
// returned by GET /runs/{id}/status — this differs from contracts' own PlanState
// (proposed/approved/rejected/...), which the running code does not use for this
// field. Flagged, not fixed: that's a Section 4 <-> contracts drift, out of scope here.
export const RunState = {
  IDLE: "idle",
  PLANNING: "planning",
  AWAITING_PLAN_APPROVAL: "awaiting_plan_approval",
  EXECUTING: "executing",
  PAUSED: "paused",
  AWAITING_DONE_SIGNAL: "awaiting_done_signal",
  REVIEWING: "reviewing",
  BLOCKED: "blocked",
  PR_OPENED: "pr_opened",
  DONE: "done",
} as const;

export type RunState = (typeof RunState)[keyof typeof RunState];

export interface RunStatus {
  runId: string;
  state: RunState;
  planId: string | null;
  planApproved: boolean;
  isHumanEditing: boolean;
}
