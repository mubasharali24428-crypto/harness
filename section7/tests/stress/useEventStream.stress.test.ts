import { describe, it, expect } from "vitest";
import { mergeEvents } from "@/lib/useEventStream";
import { EventType } from "@contracts/events";
import type { EventEnvelope } from "@contracts/events";

function evt(
  seq: number,
  type: EventType = EventType.EXECUTION_STARTED,
  payload: Record<string, unknown> = {}
): EventEnvelope {
  return { type, payload, ts: "2026-01-01T00:00:00Z", seq, projectId: "p1", runId: "r1" };
}

describe("mergeEvents - Stress & Concurrency Simulation", () => {
  it("handles inserting into empty list", () => {
    const res = mergeEvents([], evt(1));
    expect(res).toHaveLength(1);
  });

  it("maintains strict ascending sort across 100 randomly inserted seq items", () => {
    let state: EventEnvelope[] = [];
    const seqs = Array.from({ length: 100 }, (_, i) => i + 1);
    // Shuffle
    const shuffled = [...seqs].sort(() => Math.random() - 0.5);

    for (const s of shuffled) {
      state = mergeEvents(state, evt(s));
    }

    expect(state).toHaveLength(100);
    for (let i = 0; i < 99; i++) {
      expect(state[i].seq).toBeLessThan(state[i + 1].seq);
    }
  });

  it("dedupes identical seq occurrences without duplicating items", () => {
    let state = [evt(1), evt(2), evt(3)];
    state = mergeEvents(state, evt(2));
    state = mergeEvents(state, evt(1));
    state = mergeEvents(state, evt(3));
    expect(state).toHaveLength(3);
  });

  it("does not mutate original array references", () => {
    const original = [evt(1), evt(3)];
    const clone = [...original];
    const result = mergeEvents(original, evt(2));
    expect(original).toEqual(clone);
    expect(result).toHaveLength(3);
  });
});
