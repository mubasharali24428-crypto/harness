// Phase 7 planning UI — client + static render (node env, no jsdom).
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AdminPlanningView, MemberTasksView } from "./PlanningView";
import { type PlanVersion, parseTasks, reviewProblem, reviewVersion } from "./planningApi";

afterEach(() => vi.unstubAllGlobals());

const brief = {
  goals: ["ship login hardening"], constraints: [], acceptanceCriteria: ["OTP lockout audited"],
  tasks: [
    { key: "threat", title: "Threat-model the OTP flow" },
    { key: "pen", title: "External pen test", requiredRole: "pentester" },
  ],
};
const v = (version: number, state: PlanVersion["state"], penAssignee: string | null): PlanVersion => ({
  version, parentVersion: version > 1 ? version - 1 : null, state, brief, createdBy: "boss", note: null,
  assignments: [
    { task: "threat", kind: "human", dependsOn: [], assignee: "sec", rationale: "named in brief; current team member",
      uncertainty: "low", missingExpertise: [], costEstimate: "unknown" },
    { task: "pen", kind: "human", dependsOn: ["threat"], assignee: penAssignee,
      rationale: "no authorized team member named; availability unknown", uncertainty: "unknown",
      missingExpertise: penAssignee ? [] : ["pentester"], costEstimate: "unknown" },
  ],
});
const noop = () => undefined;
const admin = (versions: PlanVersion[], problem: string | null = null) =>
  renderToStaticMarkup(<AdminPlanningView versions={versions} busy={false} problem={problem} onPropose={noop} onReview={noop} />);

describe("admin planning view", () => {
  it("shows unknowns honestly: unassigned, availability unknown, cost unknown, missing expertise", () => {
    const h = admin([v(1, "proposed", null)]);
    expect(h).toContain("Unassigned: availability unknown");
    expect(h).toContain("pentester");
    expect(h).toMatch(/<td>unknown<\/td>/);
    expect(h).toContain("Approve plan");
  });
  it("older versions stay readable and read-only; only the latest carries actions", () => {
    const h = admin([v(1, "proposed", null), v(2, "approved", "sec")]);
    expect(h).toContain('data-testid="version-1"');
    expect(h).toContain("(older version, read-only)");
    expect(h.match(/data-testid="review-actions"/g)).toHaveLength(1);
    expect(h).not.toContain("Approve plan"); // latest is already approved
  });
  it("approve copy never claims work starts", () => {
    expect(admin([v(1, "proposed", null)])).toContain("Nothing starts until people pick up their tasks.");
  });
});

describe("member view", () => {
  it("lists only the caller's tasks", () => {
    const h = renderToStaticMarkup(<MemberTasksView mine={{ version: 3, tasks: [
      { task: "threat", title: "Threat-model the OTP flow", kind: "human", dependsOn: [] }] }} />);
    expect(h).toContain('data-testid="my-task-threat"');
    expect(h).not.toContain("pen");
  });
  it("says so when nothing is approved yet", () => {
    expect(renderToStaticMarkup(<MemberTasksView mine={{ version: null, tasks: [] }} />)).toContain("No approved plan yet.");
  });
});

describe("planning client", () => {
  it("review posts to the reviewed version and maps 409 to a refresh instruction", async () => {
    const fn = vi.fn(async () => new Response(JSON.stringify({ error: { code: "STALE_VERSION", message: "x" } }), { status: 409 }));
    vi.stubGlobal("fetch", fn);
    const err = await reviewVersion("p 1", "tok", 2, { decision: "approve" }).catch((e) => e);
    const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/v1\/orchestrator\/projects\/p%201\/assignment-plans\/2\/review$/);
    expect(JSON.parse(init.body as string)).toEqual({ decision: "approve" });
    expect(reviewProblem(err.status, err.code)).toContain("Refresh");
  });
  it("tasks box errors are words, not exceptions", () => {
    expect(parseTasks("nope")).toBe("Tasks are not valid JSON.");
    expect(parseTasks("[]")).toContain("non-empty");
    expect(parseTasks('[{"key":"a"}]')).toContain('"title"');
    expect(parseTasks('[{"key":"a","title":"A"}]')).toEqual([{ key: "a", title: "A" }]);
  });
});
