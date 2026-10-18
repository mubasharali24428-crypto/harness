// Pure view-model for the shared-run panel. UI gating here is PRESENTATION
// only; every action is re-authorized server-side (leader, role, run ownership).
import type { Approval, CollabEvent, RedirectKind, Roster } from "./collabApi";

export interface CollabView {
  joined: boolean;
  myRole: "leader" | "participant" | "observer" | null;
  isLeader: boolean;
  canRedirect: boolean;
  canClaimLeader: boolean;
  leader: string | null;
  pending: Approval[];
  /** Why execution is stopped, in plain words; null when nothing blocks it. */
  blockedReason: string | null;
  handoffForMe: { id: string; from: string } | null;
  handoffFromMe: { id: string; to: string } | null;
  handoffTargets: string[];
}

export function deriveCollabView(
  roster: Roster | null,
  approvals: Approval[],
  me: string,
  canLead: boolean,
): CollabView {
  const mine = roster?.participants.find((p) => p.userId === me) ?? null;
  const leader = roster?.leader ?? null;
  const pending = approvals.filter((a) => a.state === "pending");
  const h = roster?.pendingHandoff ?? null;
  const awaitingApply = approvals.filter((a) => a.requester === "system:staging" &&
    ["approved", "executing"].includes(a.state));
  return {
    joined: mine !== null,
    myRole: mine?.role ?? null,
    isLeader: leader !== null && leader === me,
    canRedirect: mine !== null && mine.role !== "observer",
    canClaimLeader: canLead && leader === null,
    leader,
    pending,
    blockedReason:
      awaitingApply.length > 0
        ? "Completion and PR creation are blocked until the leader applies the approved staged changes."
        : pending.length === 0
        ? null
        : leader
          ? `Execution is blocked until ${leader === me ? "you decide" : `${leader} decides`} ${pending.length} pending request${pending.length === 1 ? "" : "s"}.`
          : `Execution is blocked by ${pending.length} pending request${pending.length === 1 ? "" : "s"} and the run has no leader to decide.`,
    handoffForMe: h && h.to === me ? { id: h.id, from: h.from } : null,
    handoffFromMe: h && h.from === me ? { id: h.id, to: h.to } : null,
    handoffTargets:
      leader === me && !h
        ? (roster?.participants ?? []).filter((p) => p.role === "participant").map((p) => p.userId)
        : [],
  };
}

/** Scope/permission changes need a reason (server enforces too). */
export function redirectProblem(kind: RedirectKind, content: string, reason: string): string | null {
  if (!content.trim()) return "Write the direction first.";
  if (kind !== "guidance" && !reason.trim()) return "Say why — scope and permission changes need a reason.";
  return null;
}

export function describeEvent(e: CollabEvent): string {
  const who = e.actor;
  const text = typeof e.payload.content === "string" ? e.payload.content : "";
  switch (e.kind) {
    case "participant.joined": return `${who} joined as ${String(e.payload.role ?? "participant")}`;
    case "leader.assigned": return `${who} became session leader`;
    case "redirect.guidance": return `${who} gave guidance: ${text}`;
    case "redirect.scope_change": return `${who} changed the scope: ${text} (reason: ${String(e.payload.reason ?? "—")})`;
    case "redirect.permission_change": return `${who} requested a permission change: ${text} (reason: ${String(e.payload.reason ?? "—")})`;
    case "leader.takeover": return `${who} took over the lead${typeof e.payload.from === "string" ? ` from ${e.payload.from}` : ""}`;
    case "handoff.requested": return `${who} offered the lead to ${String(e.payload.to ?? "?")}`;
    case "handoff.accepted": return `${who} accepted the lead`;
    case "handoff.rejected": return `${who} declined the lead`;
    // Unknown kinds print the kind only, never the payload (nothing unvetted reaches the shared view).
    default: return `${who}: ${e.kind.replace(/[._]/g, " ")}`;
  }
}

export const APPROVAL_STATE_LABEL: Record<string, string> = {
  pending: "Waiting for leader",
  approved: "Approved",
  rejected: "Rejected",
  invalidated: "Voided — proposal or scope changed",
  expired: "Expired",
  executing: "Running",
  completed: "Done",
  failed: "Failed",
};

/** Status text for an approval; says who must act so it never relies on colour. */
export function approvalLabel(a: Approval, leader: string | null): string {
  if (a.state === "pending") return leader ? `Waiting for ${leader}` : "Waiting for a leader";
  if (a.state === "approved" && a.requester === "system:staging") return "Approved, not applied yet";
  return APPROVAL_STATE_LABEL[a.state] ?? a.state;
}

/** Latest approval status per changed path (the list arrives oldest first). */
export function fileStatuses(approvals: Approval[], leader: string | null): { path: string; state: string; label: string; approvalId: string }[] {
  const latest = new Map<string, Approval>();
  for (const a of approvals) for (const path of a.paths) latest.set(path, a);
  return [...latest].map(([path, a]) => ({ path, state: a.state, label: approvalLabel(a, leader), approvalId: a.id }));
}

/** Approvals still waiting on someone (pending decision, or approved and not yet landed). */
export const isOpenApproval = (a: Approval) => ["pending", "approved", "executing"].includes(a.state);

/** One readable line per decided approval, in request order. */
export function describeDecision(a: Approval): string {
  const what = `${a.action === "delete" ? "deleting" : "changing"} ${a.paths.join(", ")}`;
  switch (a.state) {
    case "rejected": return `${a.decidedBy ?? "Someone"} rejected ${what}`;
    case "completed": return `${a.decidedBy ?? "Someone"} approved ${what}; applied${a.outcome ? ` (${a.outcome})` : ""}`;
    case "failed": return `${a.decidedBy ?? "Someone"} approved ${what}; it failed${a.outcome ? `: ${a.outcome}` : ""}`;
    case "invalidated": return `Approval for ${what} was voided${a.outcome ? `: ${a.outcome}` : ""}`;
    case "expired": return `The request for ${what} expired before anyone decided`;
    default: return `${a.decidedBy ?? "Someone"} approved ${what}`;
  }
}

/** Approvals that went from approved back to pending with a different digest (the proposal was changed). */
export function reopenedApprovals(prev: Approval[], next: Approval[]): string[] {
  const before = new Map(prev.map((a) => [a.id, a]));
  return next.filter((a) => {
    const b = before.get(a.id);
    return b && b.state === "approved" && a.state === "pending" && b.digest !== a.digest;
  }).map((a) => a.id);
}

/** Append new history events once, in order (overlapping polls can return the same seq twice). */
export function mergeEvents(prev: CollabEvent[], next: CollabEvent[]): CollabEvent[] {
  const seen = new Set(prev.map((e) => e.seq));
  const add = next.filter((e) => !seen.has(e.seq));
  return add.length ? [...prev, ...add].sort((a, b) => a.seq - b.seq) : prev;
}

/** Cap what we render of a diff; the full text stays server-side. */
export function clipDiff(diff: string | undefined, max = 4000): string {
  const d = diff ?? "";
  return d.length > max ? `${d.slice(0, max)}\n… (${d.length - max} more characters)` : d;
}
