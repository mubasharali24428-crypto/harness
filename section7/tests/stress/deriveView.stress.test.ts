import { describe, it, expect } from "vitest";
import { deriveWorkingAreaView } from "@/lib/deriveView";
import { EventType } from "@contracts/events";
import type { EventEnvelope } from "@contracts/events";

function evt(
  seq: number,
  type: EventType,
  payload: Record<string, unknown> = {},
  ts = "2026-01-01T00:00:00Z"
): EventEnvelope {
  return { type, payload, ts, seq, projectId: "p1", runId: "r1" };
}

describe("deriveWorkingAreaView - Stress & Edge Cases", () => {
  it("handles empty event list safely", () => {
    const view = deriveWorkingAreaView([]);
    expect(view.blockerQuestion).toBeNull();
    expect(view.lastReview).toBeNull();
  });

  it("handles missing question in payload with fallback text", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.BLOCKED_AWAITING_CLARIFICATION, {}),
    ]);
    expect(view.blockerQuestion).toBe("Agent is awaiting clarification.");
  });

  it("handles null or undefined payload in blocked event gracefully", () => {
    const view = deriveWorkingAreaView([
      evt(1, EventType.BLOCKED_AWAITING_CLARIFICATION, { question: null } as any),
    ]);
    expect(view.blockerQuestion).toBe("Agent is awaiting clarification.");
  });

  it("handles complex interleaved lifecycle: started -> blocked -> resumed -> blocked again", () => {
    const events = [
      evt(1, EventType.EXECUTION_STARTED),
      evt(2, EventType.BLOCKED_AWAITING_CLARIFICATION, { question: "Q1" }),
      evt(3, EventType.RESUMED),
      evt(4, EventType.BLOCKED_AWAITING_CLARIFICATION, { question: "Q2" }),
    ];
    const view = deriveWorkingAreaView(events);
    expect(view.blockerQuestion).toBe("Q2");
  });

  it("execution_started clears prior blocker question", () => {
    const events = [
      evt(1, EventType.BLOCKED_AWAITING_CLARIFICATION, { question: "Q1" }),
      evt(2, EventType.EXECUTION_STARTED),
    ];
    const view = deriveWorkingAreaView(events);
    expect(view.blockerQuestion).toBeNull();
  });

  it("review_failed with empty findings defaults summary and issues safely", () => {
    const events = [
      evt(1, EventType.REVIEW_FAILED, {}),
    ];
    const view = deriveWorkingAreaView(events);
    expect(view.lastReview).toEqual({
      passed: false,
      summary: "",
      issues: [],
      seq: 1,
    });
  });

  it("successive reviews overwrite with the latest review", () => {
    const events = [
      evt(1, EventType.REVIEW_FAILED, { findings: { summary: "Failed first", issues: ["err1"] } }),
      evt(2, EventType.REVIEW_PASSED, { findings: { summary: "Fixed now", issues: [] } }),
    ];
    const view = deriveWorkingAreaView(events);
    expect(view.lastReview).toEqual({
      passed: true,
      summary: "Fixed now",
      issues: [],
      seq: 2,
    });
  });

  it("unrelated events do not clear lastReview", () => {
    const events = [
      evt(1, EventType.REVIEW_PASSED, { findings: { summary: "Passed", issues: [] } }),
      evt(2, EventType.PAUSED),
      evt(3, EventType.PR_OPENED),
    ];
    const view = deriveWorkingAreaView(events);
    expect(view.lastReview?.passed).toBe(true);
    expect(view.lastReview?.seq).toBe(1);
  });
});
