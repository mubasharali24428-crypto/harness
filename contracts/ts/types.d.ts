// TS mirror of harness_contracts v0.4.0 (Python is source of truth).
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
  | "pr_opened";

/** The one wire shape on the WebSocket bus (Section 1 delivery). */
export interface EventEnvelope {
  type: EventType;
  payload: Record<string, unknown>;
  ts: string;
  seq: number;
  projectId: string;
  runId?: string;
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
