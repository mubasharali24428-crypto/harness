import { describe, it, expect } from "vitest";
import { deriveWorkingAreaView } from "@/lib/deriveView";
import { EventType } from "@contracts/events";
import type { EventEnvelope } from "@contracts/events";

function evt(seq: number, type: EventType, payload: Record<string, unknown> = {}): EventEnvelope {
  return { type, payload, ts: "2026-01-01T00:00:00Z", seq, projectId: "p1", runId: "r1" };
}

describe("deriveWorkingAreaView", () => {
  it("returns no blocker and no review when the stream is empty", () => {
    const view = deriveWorkingAreaView([]);
    expect(view.blockerQuestion).toBeNull();
    expect(view.lastReview).toBeNull();
  });

  it("surfaces the clarification question while blocked", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.EXECUTION_STARTED),
      evt(2, EventType.BLOCKED_AWAITING_CLARIFICATION, { question: "Which auth lib?" }),
    ]);
    expect(view.blockerQuestion).toBe("Which auth lib?");
  });

  it("clears the blocker once the agent resumes", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.BLOCKED_AWAITING_CLARIFICATION, { question: "Which auth lib?" }),
      evt(2, EventType.RESUMED),
    ]);
    expect(view.blockerQuestion).toBeNull();
  });

  it("keeps the last review result visible even after execution resumes", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.DONE_SIGNAL_RECEIVED),
      evt(2, EventType.REVIEW_PASSED, { findings: { summary: "Looks good", issues: [] } }),
    ]);
    expect(view.lastReview).toEqual({ passed: true, summary: "Looks good", issues: [], seq: 2 });
  });

  it("reports failure with issues from review_failed", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.REVIEW_FAILED, {
        findings: { summary: "Conflicts detected", issues: ["merge conflict in auth.py"] },
      }),
    ]);
    expect(view.lastReview?.passed).toBe(false);
    expect(view.lastReview?.issues).toEqual(["merge conflict in auth.py"]);
  });
});

describe("deriveWorkingAreaView terminalLines (M2)", () => {
  it("collects terminal_output lines in order", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.TERMINAL_OUTPUT, { line: "$ pytest -q\n" }),
      evt(2, EventType.TERMINAL_OUTPUT, { line: "3 passed\n" }),
    ]);
    expect(view.terminalLines).toEqual(["$ pytest -q\n", "3 passed\n"]);
  });

  it("caps the buffer to the last 200 lines", () => {
    const events = Array.from({ length: 260 }, (_, i) =>
      evt(i + 1, EventType.TERMINAL_OUTPUT, { line: `line-${i}` })
    );
    const view = deriveWorkingAreaView(events);
    expect(view.terminalLines).toHaveLength(200);
    expect(view.terminalLines[0]).toBe("line-60");
  });

  it("ignores events without a line payload", () => {
    const view = deriveWorkingAreaView([evt(1, EventType.TERMINAL_OUTPUT, {})]);
    expect(view.terminalLines).toEqual([]);
  });

  it("does not treat diff_progress as terminal output", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.DIFF_PROGRESS, { path: "src/x.py" }),
    ]);
    expect(view.terminalLines).toEqual([]);
  });

  // M-A1: the diff panel names the files already touched, which needs the
  // diff_progress path the sandbox emits per applied edit batch.
  it("collects touched files from diff_progress, in first-seen order and de-duplicated", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.DIFF_PROGRESS, { path: "src/b.py" }),
      evt(2, EventType.DIFF_PROGRESS, { path: "src/a.py", checkpoint: { kind: "post-edit" } }),
      evt(3, EventType.DIFF_PROGRESS, { path: "src/b.py" }),
      evt(4, EventType.DIFF_PROGRESS, {}),
    ]);
    expect(view.changedPaths).toEqual(["src/b.py", "src/a.py"]);
  });

  it("reports no touched files for a run that has not edited anything", () => {
    expect(deriveWorkingAreaView([evt(1, EventType.EXECUTION_STARTED)]).changedPaths).toEqual([]);
  });
});
