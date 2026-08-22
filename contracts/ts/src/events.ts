// Mirrors contracts/src/harness_contracts/events.py. Keep in sync — additive only.

export const EventType = {
  PLAN_PROPOSED: "plan_proposed",
  PLAN_APPROVED: "plan_approved",
  EXECUTION_STARTED: "execution_started",
  PAUSED: "paused",
  RESUMED: "resumed",
  BLOCKED_AWAITING_CLARIFICATION: "blocked_awaiting_clarification",
  DONE_SIGNAL_RECEIVED: "done_signal_received",
  REVIEW_PASSED: "review_passed",
  REVIEW_FAILED: "review_failed",
  PR_OPENED: "pr_opened",
  TERMINAL_OUTPUT: "terminal_output",
  DIFF_PROGRESS: "diff_progress",
} as const;

export type EventType = (typeof EventType)[keyof typeof EventType];

export interface EventEnvelope {
  type: EventType;
  payload: Record<string, unknown>;
  ts: string;
  seq: number;
  projectId: string;
  runId: string | null;
}
