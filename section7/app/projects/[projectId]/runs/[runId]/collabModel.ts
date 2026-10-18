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
  return {
    joined: mine !== null,
    myRole: mine?.role ?? null,
    isLeader: leader !== null && leader === me,
    canRedirect: mine !== null && mine.role !== "observer",
    canClaimLeader: canLead && leader === null,
    leader,
    pending,
    blockedReason:
      pending.length === 0
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
    case "handoff.requested": return `${who} offered the lead to ${String(e.payload.to ?? "?")}`;
    case "handoff.accepted": return `${who} accepted the lead`;
    case "handoff.rejected": return `${who} declined the lead`;
    default: return `${who}: ${e.kind}`;
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

/** Cap what we render of a diff; the full text stays server-side. */
export function clipDiff(diff: string | undefined, max = 4000): string {
  const d = diff ?? "";
  return d.length > max ? `${d.slice(0, max)}\n… (${d.length - max} more characters)` : d;
}
