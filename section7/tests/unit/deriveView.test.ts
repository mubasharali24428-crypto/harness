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

describe("deriveWorkingAreaView — tool transcript", () => {
  it("pairs a call with its result and computes duration", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.TOOL_CALL, {
        callId: "c1",
        name: "bash",
        arguments: '{"command":"ls"}',
        startedAt: 1000,
      }),
      evt(2, EventType.TOOL_RESULT, {
        callId: "c1",
        ok: true,
        output: "a.txt",
        finishedAt: 1450,
      }),
    ]);
    expect(view.toolCalls).toEqual([
      {
        callId: "c1",
        name: "bash",
        arguments: '{"command":"ls"}',
        ok: true,
        error: null,
        output: "a.txt",
        durationMs: 450,
        scopeRequest: null,
      },
    ]);
  });

  it("a call without a result stays running instead of assuming success", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.TOOL_CALL, { callId: "c1", name: "edit_file" }),
    ]);
    expect(view.toolCalls[0].ok).toBeNull();
    expect(view.toolCalls[0].durationMs).toBeNull();
  });

  it("a result with no call does not invent an entry", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.TOOL_RESULT, { callId: "ghost", ok: true }),
    ]);
    expect(view.toolCalls).toEqual([]);
  });

  it("keeps call order even when results arrive out of order", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.TOOL_CALL, { callId: "c1", name: "read_file" }),
      evt(2, EventType.TOOL_CALL, { callId: "c2", name: "grep" }),
      evt(3, EventType.TOOL_RESULT, { callId: "c2", ok: true }),
      evt(4, EventType.TOOL_RESULT, { callId: "c1", ok: false, error: "ENOENT" }),
    ]);
    expect(view.toolCalls.map((c) => c.name)).toEqual(["read_file", "grep"]);
    expect(view.toolCalls[0].ok).toBe(false);
    expect(view.toolCalls[0].error).toBe("ENOENT");
    expect(view.toolCalls[1].ok).toBe(true);
  });
});

describe("deriveWorkingAreaView — context window", () => {
  it("reports the window the run started with", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.EXECUTION_STARTED, { contextWindow: 1_000_000 }),
    ]);
    expect(view.contextWindow).toBe(1_000_000);
    expect(view.compactedTokens).toBe(0);
  });

  it("sums compaction across events and keeps the latest window", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.EXECUTION_STARTED, { contextWindow: 200_000 }),
      evt(2, EventType.CONTEXT_COMPACTED, { compactedTokens: 5_000, contextWindow: 200_000 }),
      evt(3, EventType.CONTEXT_COMPACTED, { compactedTokens: 7_500, contextWindow: 1_000_000 }),
    ]);
    expect(view.contextWindow).toBe(1_000_000);
    expect(view.compactedTokens).toBe(12_500);
  });

  it("stays unknown rather than assuming a default when nothing reported one", () => {
    const view = deriveWorkingAreaView([evt(1, EventType.EXECUTION_STARTED)]);
    expect(view.contextWindow).toBeNull();
  });

  it("reports nothing about goal images when no attempt mentioned them", () => {
    expect(deriveWorkingAreaView([evt(1, EventType.EXECUTION_STARTED)]).goalImages).toBeNull();
  });

  it("shows the attached count and every declined image with its reason", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.GOAL_IMAGES, {
        requested: 3,
        attached: 1,
        refused: null,
        teamScoped: true,
        skipped: [
          { artifactId: "a2", name: "huge.png", reason: "larger than 4194304 bytes" },
          { artifactId: "a3", name: "second.png", reason: "image count limit" },
        ],
      }),
    ]);
    expect(view.goalImages).not.toBeNull();
    expect(view.goalImages?.attached).toBe(1);
    expect(view.goalImages?.skipped.map((s) => s.name)).toEqual(["huge.png", "second.png"]);
    expect(view.goalImages?.refused).toBeNull();
  });

  it("keeps the earlier attachment report when a resumed attempt sends none", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.GOAL_IMAGES, { attached: 1, skipped: [], refused: null }),
      evt(2, EventType.GOAL_IMAGES, { attached: 0, skipped: [], refused: null }),
    ]);
    expect(view.goalImages?.attached).toBe(1);
  });

  it("surfaces a deployment-level refusal", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.GOAL_IMAGES, {
        attached: 0,
        skipped: [],
        refused: "model x is not in HARNESS_VISION_MODELS; goal images were not sent",
      }),
    ]);
    expect(view.goalImages?.refused).toContain("HARNESS_VISION_MODELS");
  });

  it("ignores a malformed payload instead of inventing one", () => {
    const view = deriveWorkingAreaView([evt(1, EventType.GOAL_IMAGES, { attached: "many" })]);
    expect(view.goalImages).toBeNull();
  });
});
