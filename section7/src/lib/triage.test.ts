import { describe, expect, it } from "vitest";
import { tabTitle, tabTone, triageRuns } from "@/lib/triage";
import type { RunDto } from "../../app/runs/runsApi";

const run = (id: string, status: string, createdAt: string, startedAt: string | null = null): RunDto => ({
  id,
  projectId: "p",
  status,
  createdAt,
  startedAt,
  finishedAt: null,
});

describe("home triage (U1)", () => {
  const runs = [
    run("new-plan", "awaiting_plan_approval", "2026-11-13T10:00:00Z"),
    run("old-plan", "awaiting_plan_approval", "2026-11-13T08:00:00Z"),
    run("stuck", "blocked", "2026-11-13T09:00:00Z"),
    run("pr", "pr_opened", "2026-11-13T07:00:00Z", "2026-11-13T07:30:00Z"),
    run("busy-old", "executing", "2026-11-13T06:00:00Z"),
    run("busy-new", "planning", "2026-11-13T11:00:00Z"),
    run("finished", "done", "2026-11-13T05:00:00Z"),
    run("weird", "some_future_state", "2026-11-13T05:00:00Z"),
  ];
  const t = triageRuns(runs);

  it("puts the longest wait at the top of needs-you, with the action named", () => {
    expect(t.needsYou.map((r) => r.id)).toEqual(["pr", "old-plan", "stuck", "new-plan"]);
    expect(t.needsYou[0].action).toBe("Review the pull request");
  });
  it("lists working runs newest first and leaves finished or unknown states out", () => {
    expect(t.working.map((r) => r.id)).toEqual(["busy-new", "busy-old"]);
    const all = [...t.needsYou, ...t.working].map((r) => r.id);
    expect(all).not.toContain("finished");
    expect(all).not.toContain("weird");
  });
  it("tab title and tone follow what needs a person", () => {
    expect(tabTitle("Harness", t)).toBe("(4) Harness");
    expect(tabTone(t)).toBe("attention");
    const quiet = triageRuns([run("b", "executing", "2026-11-13T06:00:00Z")]);
    expect(tabTitle("Harness", quiet)).toBe("Harness");
    expect(tabTone(quiet)).toBe("working");
    expect(tabTone(triageRuns([]))).toBe("idle");
  });
  it("an unparseable timestamp does not crash the sort", () => {
    expect(triageRuns([run("x", "blocked", "not-a-date"), run("y", "blocked", "2026-11-13T06:00:00Z")]).needsYou).toHaveLength(2);
  });
});
