import { describe, it, expect } from "vitest";
import { mergeEvents } from "@/lib/useEventStream";
import { EventType } from "@contracts/events";
import type { EventEnvelope } from "@contracts/events";

function evt(seq: number): EventEnvelope {
  return { type: EventType.EXECUTION_STARTED, payload: {}, ts: "t", seq, projectId: "p1" };
}

describe("mergeEvents", () => {
  it("appends a new event in seq order", () => {
    const result = mergeEvents([evt(1)], evt(2));
    expect(result.map((e) => e.seq)).toEqual([1, 2]);
  });

  it("dedupes an event whose seq already exists", () => {
    const result = mergeEvents([evt(1), evt(2)], evt(2));
    expect(result).toHaveLength(2);
  });

  it("sorts out-of-order delivery by seq", () => {
    const result = mergeEvents([evt(3)], evt(1));
    expect(result.map((e) => e.seq)).toEqual([1, 3]);
  });
});
