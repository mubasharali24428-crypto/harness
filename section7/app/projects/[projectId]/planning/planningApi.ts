// Phase 7 assignment planning — client for section4 harness_orchestrator/assignment_plan.py.
// Proposal and review only: nothing here starts, dispatches or delegates work.
import { PLAN_GATE_BASE } from "../../../dashboard/planGateApi";
import { call } from "../runs/[runId]/collabApi";

export interface BriefTask {
  key: string;
  title: string;
  kind?: "human" | "agent";
  requiredRole?: string | null;
  preferredAssignee?: string | null;
  dependsOn?: string[];
}
export interface Brief {
  goals: string[];
  constraints: string[];
  acceptanceCriteria: string[];
  tasks: BriefTask[];
}
export interface Assignment {
  task: string;
  kind: "human" | "agent";
  dependsOn: string[];
  assignee: string | null;
  rationale: string;
  uncertainty: "low" | "unknown";
  missingExpertise: string[];
  costEstimate: string;
}
export interface PlanVersion {
  version: number;
  parentVersion: number | null;
  state: "proposed" | "approved" | "rejected";
  brief: Brief;
  assignments: Assignment[];
  createdBy: string;
  note: string | null;
}
export interface MyTasks {
  version: number | null;
  acceptanceCriteria?: string[];
  tasks: { task: string; title: string; kind: string; dependsOn: string[] }[];
}
export type ReviewDecision =
  | { decision: "approve" | "reject"; note?: string }
  | { decision: "edit"; note?: string; reassign: { task: string; assignee: string | null }[] };

const base = (projectId: string) =>
  `${PLAN_GATE_BASE}/api/v1/orchestrator/projects/${encodeURIComponent(projectId)}/assignment-plans`;

export const listVersions = (projectId: string, t: string) =>
  call<{ versions: PlanVersion[] }>(base(projectId), t);
export const createProposal = (projectId: string, t: string, brief: Brief) =>
  call<PlanVersion>(base(projectId), t, { method: "POST", body: JSON.stringify(brief) });
/** `version` is the version the admin reviewed; a newer one answers 409 STALE_VERSION. */
export const reviewVersion = (projectId: string, t: string, version: number, body: ReviewDecision) =>
  call<PlanVersion>(`${base(projectId)}/${version}/review`, t, { method: "POST", body: JSON.stringify(body) });
export const myTasks = (projectId: string, t: string) => call<MyTasks>(`${base(projectId)}/mine`, t);

/** One goal / constraint / criterion per line; blank lines dropped. */
export const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

/** Parse the tasks box (JSON array). Returns an error string instead of throwing. */
export function parseTasks(raw: string): BriefTask[] | string {
  try {
    const v = JSON.parse(raw) as unknown;
    if (!Array.isArray(v) || v.length === 0) return "Tasks must be a non-empty JSON array.";
    for (const t of v as BriefTask[]) {
      if (!t || typeof t.key !== "string" || typeof t.title !== "string") {
        return 'Every task needs a "key" and a "title".';
      }
    }
    return v as BriefTask[];
  } catch {
    return "Tasks are not valid JSON.";
  }
}

/** Plain words for review failures; 409 means someone else changed the plan first. */
export function reviewProblem(status: number | undefined, code: string | undefined): string {
  if (status === 409 && code === "STALE_VERSION") return "The plan changed since you opened it. Refresh, then review the latest version.";
  if (status === 409) return "This version is already decided. Refresh to see the latest version.";
  if (status === 403) return "Only project admins can plan or review assignments.";
  return "The review did not go through. Try again.";
}
