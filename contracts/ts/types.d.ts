// TS mirror of harness_contracts v1.0.0rc7 (Python is source of truth).
// Regenerate by hand only on a deliberate contracts version bump.

export type Role = "admin" | "engineer" | "viewer" | "guest";

export interface Principal {
  userId: string;
  teamId: string;
  role: Role;
}

export type EventType =
  | "plan_proposed"
  | "plan_approved"
  | "execution_started"
  | "paused"
  | "resumed"
  | "blocked_awaiting_clarification"
  | "done_signal_received"
  | "review_passed"
  | "review_failed"
  | "pr_opened"
  | "terminal_output"
  | "diff_progress"
  | "engine_completed"
  | "engine_failed"
  | "tool_call"
  | "tool_result"
  | "context_compacted"
  | "goal_images"
  | "model_routed";

/** The one wire shape on the WebSocket bus (Section 1 delivery). */
export interface EventEnvelope {
  type: EventType;
  payload: Record<string, unknown>;
  ts: string;
  seq: number;
  projectId: string;
  runId?: string;
  /** Part 14: 1 today; treat a missing field as 1 (docs/realtime.md 5.5). */
  schemaVersion?: number;
}

export interface EngineeringBrief {
  id: string;
  meetingId: string;
  projectId: string;
  requirements: string[];
  constraints: string[];
  acceptanceCriteria: string[];
  risks: string[];
  openQuestions: unknown[];
  status: "draft" | "approved";
}

export interface AgentPlan {
  goal: string;
  filesLikelyToChange: string[];
  steps: string[];
  tests: string[];
  risks: string[];
  estimatedCostUsd: number;
  forbiddenPaths: string[];
  expectedResult: string;
}

export interface ApiError {
  error: { code: string; message: string };
}

// -- 1.0.0rc7: the workflow-run contract (harness_contracts.workflow) --------

export type WorkflowStatus =
  | "ok"
  | "refused"
  | "denied"
  | "budget"
  | "cancelled"
  | "error";

/** One crossing of the host boundary, as the run reported it. */
export interface WorkflowOperation {
  callId: string;
  operation: string;
  ok: boolean;
  arguments?: string;
  error?: string | null;
  durationMs?: number;
}

export interface WorkflowRequest {
  runId: string;
  projectId: string;
  program: string;
  workflow?: string | null;
  profile?: "core" | "agent";
  grants?: string[];
  principalPermissions?: string[] | null;
  principal?: string;
  policy?: string;
  inputs?: Record<string, unknown>;
}

export interface WorkflowResult {
  status: WorkflowStatus;
  code?: string | null;
  message?: string;
  workflow?: string | null;
  output?: unknown;
  digest?: string;
  evidence?: Record<string, unknown>;
  operations?: WorkflowOperation[];
  diagnostics?: Record<string, unknown>[];
}
