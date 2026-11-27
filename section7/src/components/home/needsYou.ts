// Operability slice 1: the Home "Needs you" list as pure rules, so the node-env vitest suite
// covers them without a browser. One list, oldest wait first, one primary action (Open) per row.
//
// Source: the runs list (GET /api/v1/orchestrator/runs, contract M10-C2) and nothing else.
// It carries the three kinds below. A live scope request (a shell-gate denial mid-run) is not on
// any list API today; when the denials stop the run it arrives here as a blocked run
// (engine_failed), and the run page shows the request itself. No contract bump for this slice.
import { formatRelativeTime, formatTimestamp } from "@/lib/format";
import { needsYouHref, triageRuns } from "@/lib/triage";
import { runRowTitle } from "@/components/workspace/runRows";
import type { RunDto } from "../../../app/runs/runsApi";

export type NeedsYouKind = "approval" | "blocked" | "review";

export interface NeedsYouItem {
  id: string;
  kind: NeedsYouKind;
  /** Short kind word shown before the reason: Approval / Blocked / Review. */
  kindLabel: string;
  title: string;
  /** One line, plain words: why this needs a person. */
  reason: string;
  href: string;
  /** ISO time the wait is measured from; null when the server sent none. */
  since: string | null;
  /** "5m ago" style; "—" when the time is missing (unknown, never invented). */
  age: string;
  /** Full timestamp for the tooltip; "" when unknown. */
  sinceTitle: string;
}

/** The call whose failure the error state names. Kept as one constant so the copy and the test agree. */
export const NEEDS_YOU_CALL = "the runs list (GET /api/v1/orchestrator/runs)";

const KIND_LABEL: Record<NeedsYouKind, string> = { approval: "Approval", blocked: "Blocked", review: "Review" };

const BLOCKED_REASON: Record<string, string> = {
  question: "The agent asked a question and is waiting for an answer",
  engine_failed: "The run stopped on a failure and needs a decision",
  review_failed: "The review step failed and needs a decision",
};

function kindOf(status: string): NeedsYouKind | null {
  if (status === "awaiting_plan_approval") return "approval";
  if (status === "blocked") return "blocked";
  if (status === "pr_opened") return "review";
  return null;
}

/** One-line reason for a needs-you run. An unknown blocker says so instead of guessing. */
export function needsYouReason(run: Pick<RunDto, "status" | "planApproved" | "blockedBy">): string {
  switch (kindOf(run.status)) {
    case "approval":
      return run.planApproved ? "Plan approved; the run is waiting for someone to start it" : "Plan is waiting for your approval";
    case "blocked":
      return (run.blockedBy && BLOCKED_REASON[run.blockedBy]) || "The run is blocked; the reason was not reported";
    case "review":
      return "Pull request is open and waiting for review";
    default:
      return "";
  }
}

/** Runs → rows. Oldest wait first (triageRuns' order), so the longest-waiting item is row one. */
export function needsYouItems(runs: RunDto[], now: number = Date.now()): NeedsYouItem[] {
  return triageRuns(runs).needsYou.flatMap((run) => {
    const kind = kindOf(run.status);
    if (!kind) return [];
    const since = run.startedAt ?? run.createdAt ?? null;
    return [{
      id: run.id,
      kind,
      kindLabel: KIND_LABEL[kind],
      title: runRowTitle(run.goal),
      reason: needsYouReason(run),
      href: needsYouHref(run),
      since,
      age: since ? formatRelativeTime(since, now) : "—",
      sinceTitle: since ? formatTimestamp(since) : "",
    }];
  });
}

/** Accessible name of a row's Open link: the action, the run and why, so a list of "Open" links
 *  is never ambiguous to a screen reader. */
export function openLabel(item: Pick<NeedsYouItem, "title" | "reason">): string {
  return `Open ${item.title}: ${item.reason}`;
}

/** Focus the list after it loads only when nothing else holds focus (the page was just reached,
 *  e.g. by the "g h" shortcut, and focus is on <body> or the main region). A person already typing
 *  in the composer keeps their focus. */
export function shouldFocusList(active: { tagName?: string; id?: string } | null, mainId: string): boolean {
  if (!active) return true;
  const tag = (active.tagName ?? "").toUpperCase();
  return tag === "BODY" || tag === "HTML" || active.id === mainId;
}

/** Enter on the list itself (not on a row link, which handles Enter natively) opens the first row.
 *  Returns the href to open, or null when the key is not ours. */
export function listKeyTarget(
  ev: { key: string; target: unknown; currentTarget: unknown; altKey?: boolean; ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean },
  items: Pick<NeedsYouItem, "href">[],
): string | null {
  if (ev.key !== "Enter" || ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return null;
  if (ev.target !== ev.currentTarget) return null;
  return items[0]?.href ?? null;
}
