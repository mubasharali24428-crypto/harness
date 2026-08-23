// W1-C2 — plan-gate client + pure mapping tests. The wire shapes here ARE the
// pair-contract W1-C1↔W1-C2 seam: ATLAS diffs these against W1-C1's response
// models field-by-field at the gate, so any drift fails loudly right here.
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  PLAN_GATE_BASE,
  approvePlan,
  classifyGatePhase,
  gateActionsFor,
  gateCaption,
  proposePlan,
  rejectPlan,
  roleCanAct,
  startRun,
} from "./planGateApi";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubOk(responseBody: unknown, status = 200) {
  return vi.fn(async () => ({
    ok: true,
    status,
    text: async () => JSON.stringify(responseBody),
  }));
}

function stubError(status: number, body: unknown) {
  return vi.fn(async () => ({
    ok: false,
    status,
    text: async () => JSON.stringify(body),
  }));
}

const TOKEN = "tok-7";
const PLANS = `${PLAN_GATE_BASE}/api/v1/orchestrator/plans`;

describe("plan-gate clients — pair-contract W1-C1↔W1-C2 wire shapes", () => {
  it("proposePlan POSTs exactly {goal} to /api/v1/orchestrator/plans with bearer", async () => {
    const f = stubOk(
      { planId: "plan-9", plan: { goal: "Ship it", expectedResult: "done" } },
      201,
    );
    vi.stubGlobal("fetch", f);
    const res = await proposePlan("Ship it", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(PLANS);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ goal: "Ship it" }); // EXACTLY {goal}
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
    // DTO mirrored field-by-field: {planId, plan}
    expect(Object.keys(res).sort()).toEqual(["plan", "planId"]);
    expect(res.planId).toBe("plan-9");
    expect(res.plan.goal).toBe("Ship it");
    expect(res.plan.expectedResult).toBe("done");
  });

  it("proposePlan tolerates optional AgentPlan fields being absent on the wire", async () => {
    const f = stubOk({ planId: "p2", plan: { goal: "g", expectedResult: "e" } }, 201);
    vi.stubGlobal("fetch", f);
    const res = await proposePlan("g", TOKEN);
    expect(res.plan.steps).toBeUndefined();
    expect(res.plan.filesLikelyToChange).toBeUndefined();
    expect(res.plan.tests).toBeUndefined();
    expect(res.plan.risks).toBeUndefined();
    expect(res.plan.forbiddenPaths).toBeUndefined();
    expect(res.plan.estimatedCostUsd).toBeUndefined(); // optional on the TS mirror
  });

  it("approvePlan POSTs /plans/{id}/approve and mirrors {runId} — the server mints the run", async () => {
    const f = stubOk({ runId: "run-server-minted" });
    vi.stubGlobal("fetch", f);
    const res = await approvePlan("plan-9", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${PLANS}/plan-9/approve`);
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
    expect(Object.keys(res)).toEqual(["runId"]);
    expect(res.runId).toBe("run-server-minted");
  });

  it("rejectPlan POSTs /plans/{id}/reject (contract: 202 accepted)", async () => {
    const f = stubOk(null, 202);
    vi.stubGlobal("fetch", f);
    const res = await rejectPlan("plan-9", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${PLANS}/plan-9/reject`);
    expect(init.method).toBe("POST");
    expect(res).toBeNull();
  });

  it("startRun POSTs the orchestrator's existing /runs/{id}/start route", async () => {
    const f = stubOk({ runId: "run-1", status: "executing" });
    vi.stubGlobal("fetch", f);
    const res = await startRun("run-1", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(
      `${PLAN_GATE_BASE}/api/v1/orchestrator/runs/run-1/start`,
    );
    expect(init.method).toBe("POST");
    expect(res.status).toBe("executing");
  });

  it("surfaces the frozen error envelope as ApiError(code)", async () => {
    vi.stubGlobal(
      "fetch",
      stubError(403, {
        error: { code: "ROLE_FORBIDDEN", message: "viewer cannot approve plans" },
      }),
    );
    const err = await approvePlan("plan-9", TOKEN).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("ROLE_FORBIDDEN");
    expect((err as ApiError).status).toBe(403);
  });
});

describe("classifyGatePhase — RunStatus.state → gate phase (pure)", () => {
  it("maps the Section 4 AgentState vocabulary", () => {
    expect(classifyGatePhase(null, false)).toBe("no-plan");
    expect(classifyGatePhase(undefined, undefined)).toBe("no-plan");
    expect(classifyGatePhase("", true)).toBe("no-plan");
    expect(classifyGatePhase("planning", false)).toBe("no-plan");
    // Fresh idle run with NO plan yet: propose opens the gate.
    expect(classifyGatePhase("idle", false, false)).toBe("no-plan");
    // Rejected plan stays bound on an idle run: approve/reject again.
    expect(classifyGatePhase("idle", false, true)).toBe("awaiting-approval");
    expect(classifyGatePhase("awaiting_plan_approval", false, true)).toBe("awaiting-approval");
    // idle + planApproved=true snapshot means approved-but-not-started.
    expect(classifyGatePhase("idle", true, true)).toBe("ready-to-start");
    expect(classifyGatePhase("executing", true, true)).toBe("in-flight");
    expect(classifyGatePhase("paused", true, true)).toBe("in-flight");
    expect(classifyGatePhase("awaiting_done_signal", true, true)).toBe("in-flight");
    expect(classifyGatePhase("reviewing", true, true)).toBe("in-flight");
    expect(classifyGatePhase("pr_opened", true, true)).toBe("in-flight");
    expect(classifyGatePhase("blocked", true, true)).toBe("in-flight");
    expect(classifyGatePhase("done", true, true)).toBe("done");
  });

  it("degrades unknown server states to in-flight instead of guessing an action", () => {
    expect(classifyGatePhase("some_future_state", true)).toBe("in-flight");
  });
});

describe("gateActionsFor — phase × role × busy matrix (pure)", () => {
  const F = { propose: false, approve: false, reject: false, start: false };

  it("admin/engineer act per state", () => {
    expect(gateActionsFor("no-plan", "admin", false)).toEqual({
      propose: true,
      approve: false,
      reject: false,
      start: false,
    });
    expect(gateActionsFor("awaiting-approval", "engineer", false)).toEqual({
      propose: false,
      approve: true,
      reject: true,
      start: false,
    });
    expect(gateActionsFor("ready-to-start", "engineer", false)).toEqual({
      propose: false,
      approve: false,
      reject: false,
      start: true,
    });
  });

  it("viewer/guest never act (W1-C1 role gates: admin/engineer approve, viewer none)", () => {
    for (const role of ["viewer", "guest"]) {
      expect(gateActionsFor("no-plan", role, false)).toEqual(F);
      expect(gateActionsFor("awaiting-approval", role, false)).toEqual(F);
      expect(gateActionsFor("ready-to-start", role, false)).toEqual(F);
    }
    expect(roleCanAct("viewer")).toBe(false);
    expect(roleCanAct("guest")).toBe(false);
    expect(roleCanAct("admin")).toBe(true);
    expect(roleCanAct("engineer")).toBe(true);
    expect(roleCanAct(null)).toBe(false);
    expect(roleCanAct(undefined)).toBe(false);
  });

  it("busy disables every action (per-action busy, not a global flag)", () => {
    expect(gateActionsFor("no-plan", "admin", true)).toEqual(F);
    expect(gateActionsFor("awaiting-approval", "admin", true)).toEqual(F);
    expect(gateActionsFor("ready-to-start", "admin", true)).toEqual(F);
  });

  it("closed phases expose nothing even for admins", () => {
    expect(gateActionsFor("in-flight", "admin", false)).toEqual(F);
    expect(gateActionsFor("done", "admin", false)).toEqual(F);
  });
});

describe("gateCaption — honest captions per phase", () => {
  it("names each phase without inventing progress", () => {
    expect(gateCaption("no-plan")).toMatch(/No plan yet/i);
    expect(gateCaption("awaiting-approval")).toMatch(/awaiting approval/i);
    expect(gateCaption("ready-to-start")).toMatch(/approved/i);
    expect(gateCaption("in-flight")).toMatch(/gate is closed/i);
    expect(gateCaption("done")).toMatch(/complete/i);
  });
});
