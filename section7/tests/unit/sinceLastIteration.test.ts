// R4: which snapshot is the run's "last iteration", and how its origin is described.
import { describe, expect, it } from "vitest";
import { ApiError, type CheckpointDto, type CheckpointFile } from "@/lib/api";
import {
  isNoHistoryError,
  iterationSourceNote,
  pickLastIteration,
  visibleIterationFiles,
} from "@/lib/sinceLastIteration";

const cp = (sha: string, createdAt: string, taskId: string): CheckpointDto => ({
  sha,
  message: `checkpoint ${sha}`,
  createdAt,
  taskId,
});

describe("pickLastIteration", () => {
  it("returns null when nothing has been snapshotted", () => {
    expect(pickLastIteration([], "run-1")).toBeNull();
  });

  it("prefers the newest checkpoint tagged with this run", () => {
    const list = [
      cp("other-new", "2026-11-25T10:05:00Z", "run-2"),
      cp("mine-old", "2026-11-25T10:00:00Z", "run-1"),
      cp("mine-new", "2026-11-25T10:03:00Z", "run-1"),
    ];
    expect(pickLastIteration(list, "run-1")).toEqual({ checkpoint: list[2], linked: true });
  });

  it("falls back to the project's newest checkpoint and marks it unlinked", () => {
    const list = [
      cp("older", "2026-11-25T09:00:00Z", "task-a"),
      cp("newest", "2026-11-25T11:00:00Z", "task-b"),
    ];
    expect(pickLastIteration(list, "run-1")).toEqual({ checkpoint: list[1], linked: false });
    expect(pickLastIteration(list, null)).toEqual({ checkpoint: list[1], linked: false });
  });

  it("keeps the server's newest-first order when dates do not parse", () => {
    const list = [cp("first", "not-a-date", "t"), cp("second", "also-bad", "t")];
    expect(pickLastIteration(list, null)?.checkpoint.sha).toBe("first");
  });

  it("does not mutate the caller's list", () => {
    const list = [cp("a", "2026-11-25T09:00:00Z", "t"), cp("b", "2026-11-25T10:00:00Z", "t")];
    pickLastIteration(list, null);
    expect(list.map((c) => c.sha)).toEqual(["a", "b"]);
  });
});

describe("iterationSourceNote", () => {
  it("says plainly when the snapshot is not tagged with the run", () => {
    const checkpoint = cp("a", "2026-11-25T09:00:00Z", "t");
    expect(iterationSourceNote({ checkpoint, linked: true })).toContain("this run’s newest snapshot");
    expect(iterationSourceNote({ checkpoint, linked: false })).toContain("not tagged with this run");
  });
});

describe("visibleIterationFiles", () => {
  it("drops files with neither hunks nor a binary marker", () => {
    const files: CheckpointFile[] = [
      { path: "a.ts", binary: false, hunks: [{ index: 0, header: "@@ -1 +1 @@", lines: ["+x"], additions: 1, deletions: 0 }] },
      { path: "empty.ts", binary: false, hunks: [] },
      { path: "logo.png", binary: true, hunks: [] },
    ];
    expect(visibleIterationFiles(files).map((f) => f.path)).toEqual(["a.ts", "logo.png"]);
  });
});

describe("isNoHistoryError", () => {
  it("treats a never-snapshotted project as empty history, not a failure", () => {
    expect(isNoHistoryError(new ApiError("none", "CHECKPOINT_REPOSITORY_NOT_FOUND", 404))).toBe(true);
    expect(isNoHistoryError(new ApiError("gone", "NOT_FOUND", 404))).toBe(true);
    expect(isNoHistoryError(new ApiError("boom", "INTERNAL", 500))).toBe(false);
    expect(isNoHistoryError(new Error("network"))).toBe(false);
  });
});
