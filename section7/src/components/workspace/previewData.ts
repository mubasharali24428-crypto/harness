// R1 preview data rules — pure functions for the three preview routes
// (/preview/run, /preview/diff, /preview/review). The routes fetch through the
// SAME API clients the live pages use (app/runs/runsApi.ts, src/lib/api.ts);
// everything here only decides how the fetched data is grouped, titled and
// chosen. No fetches, no DOM, so vitest covers them in the node environment.

import type { RunDto } from "../../../app/runs/runsApi";
import { stateLabel } from "../../../app/runs/runsApi";
import { needsYouAction } from "@/lib/triage";

export type RunGroup = "needsYou" | "working" | "done";

/** States that are neither waiting on a person nor actively working. */
const DONE_STATES = new Set(["idle", "done", "completed", "stopped", "cancelled", "failed"]);

/** Which sidebar group a run belongs to. Unknown states land in "working" —
 *  they are visibly moving somewhere, so they must not read as finished. */
export function groupForRun(run: Pick<RunDto, "status">): RunGroup {
  if (needsYouAction(run as RunDto) !== null) return "needsYou";
  if (DONE_STATES.has(run.status)) return "done";
  return "working";
}

export interface GroupedRuns {
  needsYou: RunDto[];
  working: RunDto[];
  done: RunDto[];
}

/** Sidebar grouping, oldest-first inside "Needs you" (longest wait on top),
 *  newest-first everywhere else. Mirrors lib/triage's ordering rules and adds
 *  the Done group the three-pane sidebar needs. */
export function groupRuns(runs: RunDto[]): GroupedRuns {
  const age = (r: RunDto) => Date.parse(r.startedAt ?? r.createdAt) || 0;
  const out: GroupedRuns = { needsYou: [], working: [], done: [] };
  for (const run of runs) out[groupForRun(run)].push(run);
  out.needsYou.sort((a, b) => age(a) - age(b));
  out.working.sort((a, b) => age(b) - age(a));
  out.done.sort((a, b) => age(b) - age(a));
  return out;
}

/** Sidebar row title: the run's goal when the plan records one, else an honest
 *  "Untitled run". Internal ids never appear as titles (F7). */
export function runRowTitle(goal: string | null | undefined): string {
  const trimmed = (goal ?? "").trim();
  if (!trimmed) return "Untitled run";
  const firstLine = trimmed.split(/\r?\n/, 1)[0].trim();
  const cut = firstLine.length > 68 ? `${firstLine.slice(0, 67)}…` : firstLine;
  return cut || "Untitled run";
}

/** Secondary line under the title: the run's plain-language state. */
export function runRowState(run: Pick<RunDto, "status">): string {
  return stateLabel(run.status);
}

/** Which run the route shows. Precedence: ?run= deep link → the id the app
 *  remembers → the newest run. Returns null when the team has no runs (the
 *  honest empty state, never a fabricated id). */
export function pickRunId(
  runs: RunDto[],
  deepLinkRunId: string | null,
  rememberedRunId: string | null,
): string | null {
  const wanted = deepLinkRunId?.trim() || rememberedRunId?.trim() || "";
  if (wanted) {
    const hit = runs.find((r) => r.id === wanted);
    if (hit) return hit.id;
    // A deep link to a run this team cannot see is a real 404 on the detail
    // fetch; return the requested id so the route renders that error state.
    return deepLinkRunId?.trim() ? deepLinkRunId.trim() : null;
  }
  if (runs.length === 0) return null;
  const newest = [...runs].sort(
    (a, b) => Date.parse(b.startedAt ?? b.createdAt) - Date.parse(a.startedAt ?? a.createdAt),
  );
  return newest[0].id;
}

/** One unified-diff line, classified for rendering. Pure. */
export type DiffLineTone = "add" | "del" | "meta" | "context";

export interface DiffLine {
  tone: DiffLineTone;
  /** File-relative old/new line numbers where the diff records them. */
  oldNo: number | null;
  newNo: number | null;
  text: string;
}

export interface DiffFile {
  /** Path after "+++ b/…"; "unknown file" when the header is missing. */
  path: string;
  lines: DiffLine[];
}

/**
 * Minimal unified-diff parser: files from ---/+++ headers, hunks from @@, the
 * rest classified by prefix. Never throws on garbage — malformed input renders
 * as one meta block so the pane can say the diff text was unparseable.
 */
export function parseUnifiedDiff(diffText: string): DiffFile[] {
  const files: DiffFile[] = [];
  let current: DiffFile | null = null;
  let oldNo: number | null = null;
  let newNo: number | null = null;
  const push = (tone: DiffLineTone, text: string, o: number | null, n: number | null) => {
    // Header noise before the first +++ has no bucket; orphan content lines on
    // garbage input still surface, in one "(unparseable diff)" meta bucket.
    if (!current) {
      if (tone !== "meta") {
        current = { path: "(unparseable diff)", lines: [] };
        files.push(current);
      } else {
        return;
      }
    }
    current.lines.push({ tone, text, oldNo: o, newNo: n });
  };

  for (const raw of diffText.split(/\r?\n/)) {
    if (raw.startsWith("--- ") || raw.startsWith("+++ ")) {
      const path = raw.startsWith("+++ ")
        ? raw.slice(4).replace(/^b\//, "").trim()
        : raw.slice(4).replace(/^a\//, "").trim();
      if (raw.startsWith("+++ ")) {
        current = { path: path || "(unknown file)", lines: [] };
        files.push(current);
      }
      continue;
    }
    if (raw.startsWith("@@")) {
      const m = /@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw);
      oldNo = m ? Number(m[1]) : null;
      newNo = m ? Number(m[2]) : null;
      push("meta", raw, null, null);
      continue;
    }
    if (raw.startsWith("diff --git") || raw.startsWith("index ") || raw.startsWith("new file mode") || raw.startsWith("deleted file mode") || raw.startsWith("similarity ") || raw.startsWith("rename ") || raw.startsWith("Binary files")) {
      push("meta", raw, null, null);
      continue;
    }
    if (raw.startsWith("+")) {
      push("add", raw.slice(1), null, newNo);
      if (newNo !== null) newNo += 1;
      continue;
    }
    if (raw.startsWith("-")) {
      push("del", raw.slice(1), oldNo, null);
      if (oldNo !== null) oldNo += 1;
      continue;
    }
    push("context", raw.replace(/^ /, ""), oldNo, newNo);
    if (oldNo !== null) oldNo += 1;
    if (newNo !== null) newNo += 1;
  }
  return files;
}

/** Changed-file summary from the review DTO, or null when nothing changed. */
export interface DiffFileStat {
  path: string;
  additions: number;
  deletions: number;
  status: string;
}

/** "+7 −1" style summary for one file. */
export function fileStatLabel(f: DiffFileStat): string {
  const parts: string[] = [];
  if (f.additions > 0) parts.push(`+${f.additions}`);
  if (f.deletions > 0) parts.push(`−${f.deletions}`);
  return parts.length > 0 ? parts.join(" ") : "no line changes";
}
