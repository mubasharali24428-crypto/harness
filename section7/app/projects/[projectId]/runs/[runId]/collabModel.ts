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
    // Part 43: comment events carry ids only (never the text), so the line says what happened and nothing more.
    case "comment.created": return `${who} commented`;
    case "comment.edited": return `${who} edited a comment`;
    case "comment.deleted": return `${who} deleted a comment`;
    case "comment.hidden": return `${who} hid a comment`;
    case "comment.restored": return `${who} restored a comment`;
    case "comment.promoted": return `${who} sent a comment to the agent as guidance`;
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

const clip = (t: string, n: number) => (t.length > n ? `${t.slice(0, n - 1)}…` : t);

/** Part 56: what to SAY when a new approval request shows up. Silent on the first load (`before` null) and for
 *  requests already known. Urgent (assertive) only for the person who has to decide; everyone else hears it politely. */
export function approvalAnnouncements(before: Approval[] | null, next: Approval[], leader: string | null, me: string): { text: string; urgent: boolean }[] {
  if (!before) return [];
  const known = new Set(before.map((a) => a.id));
  return next.filter((a) => a.state === "pending" && !known.has(a.id)).map((a) => {
    const what = `${a.action === "delete" ? "delete" : "change"} ${clip(a.paths.join(", "), 80)}`;
    const mine = leader !== null && leader === me;
    return { text: mine ? `Approval needed from you: ${what}` : `New approval request: ${what}`, urgent: mine };
  });
}

/** Lines to announce for events that arrived now: other people's, and fresh (within `maxAgeMs`). Replaying old history is silent. */
export function freshEventLines(events: CollabEvent[], me: string, nowMs: number, maxAgeMs = 60_000): string[] {
  return events.filter((e) => e.actor !== me && nowMs - Date.parse(e.ts) < maxAgeMs).map((e) => clip(describeEvent(e), 140));
}

/** Part 24.010: what to SAY for every refusal the collaboration API can return (the error index of
 *  docs/collaboration-contract.md; `plainErrors.test.ts` fails when a documented code has no text here).
 *  No codes, no jargon: a teammate who is not an engineer must know what happened and what to do next. */
export const PLAIN_ERRORS: Record<string, string> = {
  NOT_FOUND: "That run or request is not available to you.",
  NOT_PARTICIPANT: "Join this run first, then try again.",
  NOT_LEADER: "Only the person leading this run can do that.",
  NOT_REQUESTER: "Only the person who asked for this change can edit it.",
  ROLE_DENIED: "Your role on this team can read this run but not change it.",
  SELF_APPROVAL: "Another leader has to approve your own request.",
  LEADER_EXISTS: "This run already has a leader. Ask them to hand it over.",
  LEADER_CHANGED: "The leader changed while you were doing that. Check who leads now and try again.",
  HANDOFF_PENDING: "A handoff is already waiting for an answer.",
  HANDOFF_DECIDED: "Someone else already answered this handoff.",
  SESSION_FULL: "This run has reached its limit of people. Ask the leader to make room.",
  NOT_PENDING: "This request was already decided.",
  NOT_AMENDABLE: "This request can no longer be edited.",
  NOT_APPROVED: "This change is not approved, or it was edited after it was approved. It needs a new decision.",
  NOT_EXECUTING: "This change is not being applied right now.",
  DIGEST_MISMATCH: "The proposal changed after you looked at it. Review it again before deciding.",
  EXPIRED: "This request ran out of time. Ask for it again.",
  BAD_KIND: "That kind of direction is not supported.",
  BAD_CONTENT: "Write the direction first. It cannot be empty or very long.",
  REASON_REQUIRED: "Say why. Changes to scope or permissions need a reason.",
  BAD_REASON: "The reason is too long. Shorten it.",
  SELF_HANDOFF: "You already lead this run. Pick a teammate to hand it to.",
  BAD_RECIPIENT: "Pick a teammate who has joined this run as an engineer or admin.",
  BAD_ACTION: "That kind of change is not supported.",
  BAD_PROPOSAL: "Describe the change in a few words. The description or the diff is empty or too large.",
  BAD_PATHS: "One of the file paths is not allowed. Use paths inside the project.",
};

/** The plain sentence for an API refusal, or null when the code is not one of ours. */
export function plainError(e: unknown): string | null {
  const code = e && typeof e === "object" && "code" in e ? String((e as { code: unknown }).code) : "";
  return Object.prototype.hasOwnProperty.call(PLAIN_ERRORS, code) ? PLAIN_ERRORS[code] : null;
}
