import { describe, expect, it } from "vitest";
import { buildTrace, formatMs } from "@/components/workspace/trace";

const at = (sec: number) => new Date(Date.UTC(2026, 0, 1, 0, 0, sec)).toISOString();

describe("trace", () => {
  it("places events by their own timestamps and uses the gap to the next event", () => {
    const t = buildTrace([
      { seq: 1, type: "plan_proposed", ts: at(0) },
      { seq: 2, type: "tool_call", ts: at(10) },
      { seq: 3, type: "pr_opened", ts: at(40) },
    ]);
    expect(t.totalMs).toBe(40_000);
    expect(t.rows.map((r) => [r.offsetMs, r.durationMs, r.measured])).toEqual([
      [0, 10_000, false], [10_000, 30_000, false], [40_000, 0, false],
    ]);
    expect(t.rows[1].leftPct).toBe(25);
    expect(t.rows[1].widthPct).toBe(75);
    expect(t.rows[2].widthPct).toBe(1); // a zero-length event is still visible
  });

  it("prefers the server's measured duration", () => {
    const t = buildTrace([
      { seq: 1, type: "tool_result", ts: at(0), detail: { durationMs: 2500 } },
      { seq: 2, type: "pr_opened", ts: at(10) },
    ]);
    expect(t.rows[0]).toMatchObject({ durationMs: 2500, measured: true });
  });

  it("leaves out events with no recorded time and says how many", () => {
    const t = buildTrace([{ seq: 1, type: "a", ts: null }, { seq: 2, type: "b", ts: "nonsense" }]);
    expect(t).toEqual({ rows: [], totalMs: 0, untimed: 2 });
  });

  it("formats durations", () => {
    expect([formatMs(850), formatMs(12_400), formatMs(185_000)]).toEqual(["850 ms", "12.4 s", "3 min 05 s"]);
  });
});
