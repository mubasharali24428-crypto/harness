// Raw contract event types ("plan.proposed", "engine.failed") read like log
// lines in a timeline. These helpers give them a human label and a tone; the
// timeline never re-derives state from them, it only presents what landed.

const LABELS: Record<string, string> = {
  "plan.proposed": "Plan proposed",
  "plan.approved": "Plan approved",
  "plan.rejected": "Plan rejected",
  "execution.started": "Execution started",
  "engine.completed": "Engine finished",
  "engine.failed": "Engine failed",
  paused: "Run paused",
  resumed: "Run resumed",
  "review.passed": "Review passed",
  "review.failed": "Review failed",
  "pr.opened": "Pull request opened",
  "completion.reported": "Completion reported",
  "run.abandoned": "Run abandoned",
  "run.retry_requested": "Retry requested",
  "subtask.created": "Subtask added",
  "subtask.completed": "Subtask completed",
};

export type EventTone = "danger" | "success" | "accent";

export function eventLabel(type: string): string {
  const known = LABELS[type];
  if (known) return known;
  const clean = type.replace(/[._]/g, " ").trim();
  return clean ? clean.charAt(0).toUpperCase() + clean.slice(1) : "Event";
}

export function eventTone(type: string): EventTone {
  if (/fail|error|blocked|abandon/i.test(type)) return "danger";
  if (/complete|approved|passed|done/i.test(type)) return "success";
  return "accent";
}
