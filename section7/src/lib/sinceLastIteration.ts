// R4 "since last iteration": which snapshot is the run's last iteration, and
// how the pane describes where it came from. Pure, so vitest's node
// environment proves the choice without a DOM.
//
// An iteration is one recorded edit batch: a sandbox checkpoint. The sandbox
// lists a project's checkpoints newest first and tags each with the task
// workspace it came from (`taskId`). The newest checkpoint tagged with this
// run wins; when none carries the run's id, the newest one in the project is
// used and the pane says so, instead of pretending it belongs to the run.

import type { CheckpointDto, CheckpointFile } from "./api";
import { ApiError } from "./api";

export interface IterationPick {
  checkpoint: CheckpointDto;
  /** True when the checkpoint's taskId is this run's id. */
  linked: boolean;
}

function newestFirst(a: CheckpointDto, b: CheckpointDto): number {
  const ta = Date.parse(a.createdAt);
  const tb = Date.parse(b.createdAt);
  // Unparseable dates keep the server's order (it already sorts newest first).
  if (Number.isNaN(ta) || Number.isNaN(tb)) return 0;
  return tb - ta;
}

/** The newest checkpoint for this run, else the newest in the project, else null. */
export function pickLastIteration(
  snapshots: readonly CheckpointDto[],
  runId: string | null,
): IterationPick | null {
  if (snapshots.length === 0) return null;
  const sorted = [...snapshots].sort(newestFirst);
  if (runId) {
    const own = sorted.find((s) => s.taskId === runId);
    if (own) return { checkpoint: own, linked: true };
  }
  return { checkpoint: sorted[0], linked: false };
}

/** One sentence naming the snapshot's origin. */
export function iterationSourceNote(pick: IterationPick): string {
  return pick.linked
    ? "Changes in this run’s newest snapshot."
    : "Changes in the project’s newest snapshot. It is not tagged with this run.";
}

/** Files that have something to show: hunks, or a binary marker. */
export function visibleIterationFiles(files: readonly CheckpointFile[]): CheckpointFile[] {
  return files.filter((f) => f.binary || f.hunks.length > 0);
}

/**
 * A project the sandbox never snapshotted answers 404
 * CHECKPOINT_REPOSITORY_NOT_FOUND: that is "no iterations yet", not a failure.
 */
export function isNoHistoryError(e: unknown): boolean {
  return e instanceof ApiError && (e.code === "CHECKPOINT_REPOSITORY_NOT_FOUND" || e.status === 404);
}
