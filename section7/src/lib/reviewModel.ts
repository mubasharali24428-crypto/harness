// A4-S1 (F007/F009): pure model for per-hunk change-set review. No I/O, no React —
// every integrity rule the UI enforces is testable in the node-env vitest runner.

export type Decision = "pending" | "accept" | "reject";

export interface HunkLine {
  kind: "ctx" | "add" | "del";
  text: string;
}

export interface Hunk {
  id: string;
  oldStart: number;
  oldCount: number;
  newStart: number;
  newCount: number;
  lines: HunkLine[];
}

export interface FileProposal {
  path: string;
  baseSha256: string | null;
  drift?: "current" | "stale" | "file-missing";
  hunks: Hunk[];
  decisions: Record<string, { decision: Decision; comment?: string | null }>;
}

export interface ChangeSet {
  changeSetId: string;
  sessionId: string;
  title: string;
  status: "open" | "applied" | "cancelled";
  files: FileProposal[];
}

export interface ReviewSummary {
  accepted: number;
  rejected: number;
  pending: number;
  staleFiles: string[];
  canApply: boolean;
  blocker: string | null;
}

export function decisionOf(file: FileProposal, hunkId: string): Decision {
  return file.decisions[hunkId]?.decision ?? "pending";
}

export function summarize(cs: ChangeSet, canWrite: boolean): ReviewSummary {
  let accepted = 0;
  let rejected = 0;
  let pending = 0;
  const staleFiles: string[] = [];
  for (const f of cs.files) {
    // Drift is only meaningful while the change set is still open; after apply the
    // workspace legitimately differs from the original base.
    if (cs.status === "open" && f.drift && f.drift !== "current") staleFiles.push(f.path);
    for (const h of f.hunks) {
      const d = decisionOf(f, h.id);
      if (d === "accept") accepted += 1;
      else if (d === "reject") rejected += 1;
      else pending += 1;
    }
  }
  let blocker: string | null = null;
  if (!canWrite) blocker = "Your role is read-only; you can review but not decide or apply.";
  else if (cs.status !== "open") blocker = `This change set is ${cs.status}.`;
  else if (staleFiles.length) blocker = `Workspace changed under: ${staleFiles.join(", ")}. Re-create the change set.`;
  else if (accepted === 0) blocker = "Accept at least one hunk to apply.";
  return { accepted, rejected, pending, staleFiles, canApply: blocker === null, blocker };
}

/** Flat, ordered list of (path, hunkId) so keyboard navigation crosses files. */
export function flatHunks(cs: ChangeSet): { path: string; hunkId: string }[] {
  return cs.files.flatMap((f) => f.hunks.map((h) => ({ path: f.path, hunkId: h.id })));
}

export type KeyIntent =
  | { type: "move"; delta: 1 | -1 }
  | { type: "decide"; decision: Decision }
  | { type: "comment" }
  | { type: "blur" }
  | null;

/** Maps a key press to an intent. Modified keys and text-entry targets never trigger it. */
export function keyIntent(e: {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  targetIsTextEntry?: boolean;
}): KeyIntent {
  if (e.key === "Escape") return { type: "blur" };
  if (e.ctrlKey || e.metaKey || e.altKey || e.targetIsTextEntry) return null;
  switch (e.key) {
    case "j":
      return { type: "move", delta: 1 };
    case "k":
      return { type: "move", delta: -1 };
    case "a":
      return { type: "decide", decision: "accept" };
    case "r":
      return { type: "decide", decision: "reject" };
    case "u":
      return { type: "decide", decision: "pending" };
    case "c":
      return { type: "comment" };
    default:
      return null;
  }
}

export function moveIndex(current: number, delta: 1 | -1, length: number): number {
  if (length <= 0) return -1;
  if (current < 0) return delta === 1 ? 0 : length - 1;
  return Math.min(length - 1, Math.max(0, current + delta));
}

/** Optimistic local update; the server remains the source of truth and wins on refetch. */
export function withDecision(cs: ChangeSet, path: string, hunkId: string, decision: Decision, comment?: string): ChangeSet {
  return {
    ...cs,
    files: cs.files.map((f) =>
      f.path !== path
        ? f
        : { ...f, decisions: { ...f.decisions, [hunkId]: { decision, comment: comment ?? f.decisions[hunkId]?.comment ?? null } } },
    ),
  };
}
