// U1 home: which runs need a person now, and which are working. Pure, so the rules are tested
// without a browser. States are section4's AgentState (app/runs/runsApi.ts RUN_STATUSES).
import type { RunDto } from "../../app/runs/runsApi";

/** States where nothing moves until a person acts, with the action the inbox names. */
export const NEEDS_YOU: Record<string, string> = {
  awaiting_plan_approval: "Approve or reject the plan",
  blocked: "Answer the agent's question",
  pr_opened: "Review the pull request",
};

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
    .map((r) => ({ ...r, action: NEEDS_YOU[r.status] }))
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
