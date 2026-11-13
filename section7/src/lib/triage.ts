// U1 home: which runs need a person now, and which are working. Pure, so the rules are tested
// without a browser. States are section4's AgentState (app/runs/runsApi.ts RUN_STATUSES).
import { runDetailHref, type RunDto } from "../../app/runs/runsApi";

/** States where nothing moves until a person acts, with the default action the inbox names.
 *  `needsYouAction` refines it from the run's planApproved / blockedBy fields. */
export const NEEDS_YOU: Record<string, string> = {
  awaiting_plan_approval: "Approve or reject the plan",
  blocked: "Decide how to unblock the run",
  pr_opened: "Review the pull request",
};

const BLOCKED_ACTION: Record<string, string> = {
  question: "Answer the agent's question",
  engine_failed: "The run failed: decide what to do next",
  review_failed: "The review failed: decide what to do next",
};

/** The action a person must take on this run, or null when it does not need a person. The
 *  state stays awaiting_plan_approval after approval (the run still has to be started), and
 *  "blocked" covers a question, an engine failure and a failed review alike. */
export function needsYouAction(run: Pick<RunDto, "status" | "planApproved" | "blockedBy">): string | null {
  if (run.status === "awaiting_plan_approval" && run.planApproved) return "Start the approved run";
  if (run.status === "blocked" && run.blockedBy && BLOCKED_ACTION[run.blockedBy]) {
    return BLOCKED_ACTION[run.blockedBy];
  }
  return NEEDS_YOU[run.status] ?? null;
}

/** Where the inbox link lands: approval and start both live on the run's "Plan & work" tab. */
export function needsYouHref(run: Pick<RunDto, "id" | "projectId" | "status">): string {
  const base = runDetailHref(run);
  return run.status === "awaiting_plan_approval" ? `${base}?tab=plan` : base;
}

/** States where an agent (or the review step) is working. */
export const WORKING = new Set(["planning", "executing", "paused", "awaiting_done_signal", "reviewing"]);

export interface Triage {
  needsYou: Array<RunDto & { action: string }>;
  working: RunDto[];
}

function ts(run: RunDto): number {
  const t = Date.parse(run.startedAt ?? run.createdAt);
  return Number.isNaN(t) ? 0 : t;
}

/** Needs-you oldest first (longest wait at the top); working newest first. */
export function triageRuns(runs: RunDto[]): Triage {
  const needsYou = runs
    .filter((r) => r.status in NEEDS_YOU)
    .map((r) => ({ ...r, action: needsYouAction(r) ?? NEEDS_YOU[r.status] }))
    .sort((a, b) => ts(a) - ts(b));
  const working = runs.filter((r) => WORKING.has(r.status)).sort((a, b) => ts(b) - ts(a));
  return { needsYou, working };
}

/** Browser tab title: the count that needs you comes first, so it shows in a crowded tab bar. */
export function tabTitle(base: string, t: Triage): string {
  return t.needsYou.length > 0 ? `(${t.needsYou.length}) ${base}` : base;
}

/** Tab icon colour: needs you > working > idle. */
export function tabTone(t: Triage): "attention" | "working" | "idle" {
  if (t.needsYou.length > 0) return "attention";
  return t.working.length > 0 ? "working" : "idle";
}
