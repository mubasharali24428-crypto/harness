// M12-E4b — review-page prId consumer: the three acquisition paths.
//   1. deep link ?pr=  (param)
//   2. pr_opened event on the bus (event)
//   3. neither produced anything → honest-empty (null → page renders
//      PrEmptyState with PR_PENDING_COPY)
// Pure logic only (node env, no jsdom), mirroring tests/unit conventions.
import { describe, expect, it } from "vitest";
import { EventType } from "@contracts/events";
import type { EventEnvelope } from "@contracts/events";
import {
  adoptEventPrId,
  prIdFromEvent,
  prIdFromSearch,
  resolvePrId,
} from "./prSource";

function envelope(partial: Partial<EventEnvelope> & { seq: number }): EventEnvelope {
  return {
    type: EventType.PR_OPENED,
    payload: { runId: "run-1", prId: "42", url: "https://github.com/acme/app/pull/42" },
    ts: "2026-08-23T00:00:00Z",
    projectId: "proj-1",
    ...partial,
  };
}

const OPENED_RUN1 = envelope({ seq: 7 });

describe("path 1 — ?pr= query param", () => {
  it("reads pr from a full search string", () => {
    expect(prIdFromSearch("?pr=42")).toBe("42");
  });

  it("accepts a bare query without the leading ?", () => {
    expect(prIdFromSearch("pr=17")).toBe("17");
  });

  it("trims whitespace around the value", () => {
    expect(prIdFromSearch("?pr=%2042%20")).toBe("42");
  });

  it("returns null when absent or blank", () => {
    expect(prIdFromSearch("")).toBeNull();
    expect(prIdFromSearch("?run=9")).toBeNull();
    expect(prIdFromSearch("?pr=")).toBeNull();
    expect(prIdFromSearch("?pr=%20%20")).toBeNull();
  });

  it("deep link wins over any remembered value", () => {
    expect(resolvePrId("?pr=42", "99")).toBe("42");
  });

  it("falls back to the session-remembered value when no param", () => {
    expect(resolvePrId("", "99")).toBe("99");
  });
});

describe("path 2 — pr_opened bus event", () => {
  it("adopts prId when the event matches the current run", () => {
    expect(prIdFromEvent(OPENED_RUN1, "run-1")).toBe("42");
    expect(adoptEventPrId(null, [OPENED_RUN1], "run-1")).toBe("42");
  });

  it("ignores events for other runs", () => {
    const other = envelope({ seq: 8, payload: { runId: "run-2", prId: "43", url: "u" } });
    expect(prIdFromEvent(other, "run-1")).toBeNull();
    expect(adoptEventPrId(null, [other], "run-1")).toBeNull();
  });

  it("ignores pr_opened when no current run is known", () => {
    expect(prIdFromEvent(OPENED_RUN1, null)).toBeNull();
    expect(adoptEventPrId(null, [OPENED_RUN1], null)).toBeNull();
  });

  it("falls back to the envelope-level runId when payload omits it", () => {
    const envLevel = envelope({
      seq: 9,
      runId: "run-1",
      payload: { prId: "44", url: "u" },
    });
    expect(prIdFromEvent(envLevel, "run-1")).toBe("44");
  });

  it("ignores non-pr_opened envelopes entirely", () => {
    const noise: EventEnvelope = {
      type: EventType.TERMINAL_OUTPUT,
      payload: { runId: "run-1", prId: "42" },
      ts: "t",
      seq: 10,
      projectId: "proj-1",
    };
    expect(adoptEventPrId(null, [noise], "run-1")).toBeNull();
  });

  it("returns null when pr_opened carries no usable prId", () => {
    const broken = envelope({ seq: 11, payload: { runId: "run-1", url: "u" } });
    expect(prIdFromEvent(broken, "run-1")).toBeNull();
  });

  it("picks the NEWEST matching event when several exist", () => {
    const older = envelope({ seq: 5 });
    const newer = envelope({ seq: 6, payload: { runId: "run-1", prId: "45", url: "u" } });
    expect(adoptEventPrId(null, [older, newer], "run-1")).toBe("45");
  });

  it("never clobbers an already-held prId (replay safety)", () => {
    expect(adoptEventPrId("99", [OPENED_RUN1], "run-1")).toBeNull();
  });

  it("is inert on an empty stream (honest-empty path precondition)", () => {
    expect(adoptEventPrId(null, [], "run-1")).toBeNull();
  });
});

describe("path 3 — honest empty", () => {
  it("resolves to null from every source at once", () => {
    expect(resolvePrId("", null)).toBeNull();
    expect(resolvePrId("", "")).toBeNull();
    expect(resolvePrId("?other=x", null)).toBeNull();
  });

  it("null survives an event stream that never matched", () => {
    const otherRun = envelope({ seq: 12, payload: { runId: "run-X", prId: "50", url: "u" } });
    expect(adoptEventPrId(resolvePrId("", null), [otherRun], null)).toBeNull();
  });

  it("treats whitespace-only stored values as absent", () => {
    expect(resolvePrId("", "   ")).toBeNull();
  });
});

describe("runIdForPr (verdict card must not borrow another run's facts)", () => {
  const opened = (seq: number, runId: string, prId: string) =>
    envelope({ seq, type: EventType.PR_OPENED, runId, payload: { runId, prId, url: "u" } });

  it("returns the run that opened this PR, not the newest run on the stream", async () => {
    const { runIdForPr } = await import("./prSource");
    const events = [
      opened(1, "run-a", "pr-1"),
      envelope({ seq: 2, type: EventType.EXECUTION_STARTED, runId: "run-b", payload: { runId: "run-b" } }),
    ];
    expect(runIdForPr("pr-1", events)).toBe("run-a");
  });

  it("is null when no event links the PR to a run", async () => {
    const { runIdForPr } = await import("./prSource");
    expect(runIdForPr("pr-9", [opened(1, "run-a", "pr-1")])).toBeNull();
    expect(runIdForPr(null, [opened(1, "run-a", "pr-1")])).toBeNull();
    expect(runIdForPr("pr-1", [])).toBeNull();
  });
});
