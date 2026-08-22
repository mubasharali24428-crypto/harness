/**
 * S9 conformance — the mock layer must satisfy the frozen contracts (v0.2.0 TS mirror)
 * so checkpoint-2 can swap mocks for real endpoints without shape changes.
 */
import { describe, expect, it } from "vitest";
import type {
  AgentPlan,
  EngineeringBrief,
  EventEnvelope,
  EventType,
  Principal,
} from "@contracts";

import {
  mockBrief,
  mockDiff,
  mockEvents,
  mockPlan,
  mockPrincipal,
  mockTeams,
} from "@/lib/mockApi";

const ALL_EVENT_TYPES: EventType[] = [
  "plan_proposed",
  "plan_approved",
  "execution_started",
  "paused",
  "resumed",
  "blocked_awaiting_clarification",
  "done_signal_received",
  "review_passed",
  "review_failed",
  "pr_opened",
];

describe("mock payloads conform to frozen contracts", () => {
  it("principal has exactly userId/teamId/role with a valid role", () => {
    const p: Principal = mockPrincipal;
    expect(Object.keys(p).sort()).toEqual(["role", "teamId", "userId"]);
    expect(["admin", "engineer", "viewer", "guest"]).toContain(p.role);
  });

  it("event envelopes carry the exact wire shape and known types", () => {
    const events: EventEnvelope[] = mockEvents();
    expect(events.length).toBeGreaterThan(0);
    for (const e of events) {
      expect(Object.keys(e).sort()).toEqual([
        "payload",
        "projectId",
        "runId",
        "seq",
        "ts",
        "type",
      ]);
      expect(ALL_EVENT_TYPES).toContain(e.type);
      expect(typeof e.seq).toBe("number");
      expect(Number.isNaN(Date.parse(e.ts))).toBe(false);
    }
    const seqs = events.map((e) => e.seq);
    expect(new Set(seqs).size).toBe(seqs.length), "seqs unique";
  });

  it("agent plan has exactly the eight brief-mandated fields", () => {
    const plan: AgentPlan = mockPlan;
    expect(Object.keys(plan).sort()).toEqual(
      [
        "estimatedCostUsd",
        "expectedResult",
        "filesLikelyToChange",
        "forbiddenPaths",
        "goal",
        "risks",
        "steps",
        "tests",
      ].sort()
    );
    expect(plan.forbiddenPaths.length).toBeGreaterThan(0);
  });

  it("engineering brief carries all five fields plus lifecycle", () => {
    const brief: EngineeringBrief = mockBrief;
    for (const field of [
      "requirements",
      "constraints",
      "acceptanceCriteria",
      "risks",
      "openQuestions",
    ] as const) {
      expect(Array.isArray(brief[field])).toBe(true);
    }
    expect(["draft", "approved"]).toContain(brief.status);
  });

  it("diff fixture provides original+modified strings for MergeView", () => {
    const d = mockDiff();
    expect(typeof d.original).toBe("string");
    expect(typeof d.modified).toBe("string");
    expect(d.modified).not.toEqual(d.original);
  });

  it("team switcher data is non-empty with projects", () => {
    expect(mockTeams.length).toBeGreaterThan(0);
    for (const t of mockTeams) {
      expect(t.projects.length).toBeGreaterThan(0);
    }
  });
});
