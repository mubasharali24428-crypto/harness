// Run-row helpers for the sidebar, triage and run pages: grouping, titles,
// states and run selection. Pure functions over the runs DTO — no fetches, no
// DOM, so vitest covers them in the node environment.
//
// E0 2026-11-21: moved verbatim out of previewData.ts (a misnamed R1 module
// that held pure helpers, not sample data). Shipped routes import from here;
// previewData.ts is gone and a unit guard forbids re-importing it.

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
