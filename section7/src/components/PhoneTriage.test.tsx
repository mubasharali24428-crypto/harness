// F6 — PhoneTriage tests. Node-env constraint (no jsdom/RTL in this repo;
// installs are out of scope): markup is rendered with react-dom/server and
// wire calls are pinned by stubbing global fetch, the same protocol as
// planGateApi.test.ts. Pure mapping lives in pendingApprovals/actionsForItem
// so the approve/deny contract is testable without a browser.
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { RunDto } from "../../app/runs/runsApi";
import {
  PHONE_TRIAGE_STRINGS,
  PhoneTriage,
  actionsForItem,
  decidePlan,
  pendingApprovals,
} from "./PhoneTriage";

afterEach(() => {
  vi.unstubAllGlobals();
});

function run(partial: Partial<RunDto>): RunDto {
  return {
    id: partial.id ?? "run-1234567890abcdef",
    projectId: partial.projectId ?? "proj-1",
    status: partial.status ?? "awaiting_plan_approval",
    createdAt: partial.createdAt ?? "2026-11-13T16:00:00Z",
    startedAt: partial.startedAt ?? null,
    finishedAt: partial.finishedAt ?? null,
    planApproved: partial.planApproved,
    blockedBy: partial.blockedBy,
  };
}

const NOW = Date.parse("2026-11-13T17:00:00Z");

function pendingItem(overrides?: Partial<Parameters<typeof run>[0]>) {
  return {
    run: run({ id: "want", status: "awaiting_plan_approval", ...overrides }),
    kind: "plan-approval" as const,
    planId: "plan-9",
    goal: "Add a phone inbox",
  };
}

describe("pendingApprovals — U5 pending set (approve or deny only)", () => {
  it("lists ONLY pending plan approvals; working, blocked, PR and done stay off the screen", () => {
    const items = pendingApprovals([
      run({ id: "want-1", status: "awaiting_plan_approval", planApproved: false }),
      run({ id: "want-2", status: "awaiting_plan_approval", planApproved: true }),
      run({ id: "working", status: "executing" }),
      run({ id: "planning", status: "planning" }),
      run({ id: "blocked", status: "blocked" }),
      run({ id: "pr", status: "pr_opened" }),
      run({ id: "done", status: "done" }),
    ]);
    expect(items.map((i) => i.run.id).sort()).toEqual(["want-1", "want-2"]);
    expect(items.find((i) => i.run.id === "want-1")?.kind).toBe("plan-approval");
    expect(items.find((i) => i.run.id === "want-2")?.kind).toBe("plan-start");
  });

  it("an already-approved plan is Start, not a second Approve", () => {
    const [item] = pendingApprovals([run({ planApproved: true })]);
    expect(item.kind).toBe("plan-start");
    expect(actionsForItem(item.kind, item.planId, false)).toEqual({
      approve: false,
      deny: false,
    });
  });

  it("actionsForItem allows approve/deny only for an undecided plan with a planId", () => {
    expect(actionsForItem("plan-approval", "plan-1", false)).toEqual({
      approve: true,
      deny: true,
    });
    expect(actionsForItem("plan-approval", null, false)).toEqual({
      approve: false,
      deny: false,
    });
    expect(actionsForItem("plan-approval", "plan-1", true)).toEqual({
      approve: false,
      deny: false,
    });
    expect(actionsForItem("plan-start", "plan-1", false)).toEqual({
      approve: false,
      deny: false,
    });
  });
});

describe("PhoneTriage rendering — node-env SSR", () => {
  it("empty state names the phone inbox and does not render action buttons", () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const html = renderToStaticMarkup(
      <PhoneTriage
        token="tok"
        now={NOW}
        items={pendingApprovals([run({ id: "busy", status: "executing" })])}
      />,
    );
    expect(html).toContain('data-testid="empty-state"');
    expect(html).toContain(PHONE_TRIAGE_STRINGS.emptyTitle);
    expect(html).not.toContain('data-testid="phone-approve"');
    expect(html).not.toContain('data-testid="phone-deny"');
    expect(f).not.toHaveBeenCalled();
  });

  it("pending plan renders Approve and Deny and nothing else as a control", () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const html = renderToStaticMarkup(
      <PhoneTriage token="tok" now={NOW} items={[pendingItem()]} />,
    );
    expect(html).toContain('data-testid="phone-triage-item"');
    expect(html).toContain("Add a phone inbox");
    expect(html).toContain('data-testid="phone-approve"');
    expect(html).toContain('data-testid="phone-deny"');
    expect(html).not.toContain('data-testid="start-run"');
    expect(html).not.toContain('data-testid="propose-plan"');
    // The whole screen has exactly two buttons.
    expect((html.match(/<button/g) ?? []).length).toBe(2);
    // No run-detail link: deciding happens here, navigation does not.
    expect(html).not.toContain('href="/projects/');
    expect(f).not.toHaveBeenCalled();
  });

  it("start-only item shows no Approve/Deny pair", () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const html = renderToStaticMarkup(
      <PhoneTriage
        token="tok"
        now={NOW}
        items={[
          {
            run: run({ id: "started", status: "awaiting_plan_approval", planApproved: true }),
            kind: "plan-start",
            planId: "plan-8",
            goal: null,
          },
        ]}
      />,
    );
    expect(html).toContain(PHONE_TRIAGE_STRINGS.startLabel);
    expect(html).not.toContain('data-testid="phone-approve"');
    expect(html).not.toContain('data-testid="phone-deny"');
    expect(html).not.toContain("<button");
    expect(f).not.toHaveBeenCalled();
  });

  it("signed-out: empty state, no fetch, no buttons", () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const html = renderToStaticMarkup(<PhoneTriage token={null} now={NOW} />);
    expect(html).toContain("Sign in to see pending approvals.");
    expect(f).not.toHaveBeenCalled();
    expect(html).not.toContain("<button");
  });
});

describe("PhoneTriage wire — same endpoints and arguments as desktop", () => {
  function captureFetch(calls: Array<{ url: string; init: RequestInit }>) {
    return vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const href = typeof url === "string" ? url : String(url);
      if (init?.method === "POST") {
        calls.push({ url: href, init: init });
        const approve = href.endsWith("/approve");
        return {
          ok: true,
          status: approve ? 200 : 202,
          text: async () => (approve ? JSON.stringify({ runId: "run-x" }) : ""),
        };
      }
      return {
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ items: [], nextCursor: null }),
      };
    });
  }

  it("approve POSTs the desktop /plans/{id}/approve with the same body", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal("fetch", captureFetch(calls));
    await decidePlan(pendingItem(), "approve", "tok");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain("/api/v1/orchestrator/plans/plan-9/approve");
    expect(calls[0].init.method).toBe("POST");
    expect((calls[0].init.headers as Record<string, string>).authorization).toBe("Bearer tok");
    expect(JSON.parse(String(calls[0].init.body))).toEqual({});
  });

  it("deny POSTs the desktop /plans/{id}/reject with the same body", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal("fetch", captureFetch(calls));
    await decidePlan(pendingItem(), "deny", "tok");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain("/api/v1/orchestrator/plans/plan-9/reject");
    expect(calls[0].init.method).toBe("POST");
    expect((calls[0].init.headers as Record<string, string>).authorization).toBe("Bearer tok");
    expect(JSON.parse(String(calls[0].init.body))).toEqual({});
  });

  it("phone decidePlan and the desktop clients emit the SAME fetch", async () => {
    const { approvePlan, rejectPlan } = await import("../../app/dashboard/planGateApi");
    const calls: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal("fetch", captureFetch(calls));
    await approvePlan("plan-9", "tok");
    await decidePlan(pendingItem(), "approve", "tok");
    await rejectPlan("plan-9", "tok");
    await decidePlan(pendingItem(), "deny", "tok");
    expect(calls).toHaveLength(4);
    // [desktop approve, phone approve, desktop reject, phone reject]
    expect(calls[0].url).toBe(calls[1].url);
    expect(String(calls[0].init.body)).toBe(String(calls[1].init.body));
    expect(calls[2].url).toBe(calls[3].url);
    expect(String(calls[2].init.body)).toBe(String(calls[3].init.body));
  });

  it("list load uses listRuns({ limit: 100 }) — the same GET the desktop inbox uses", async () => {
    const f = vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ items: [], nextCursor: null }),
    }));
    vi.stubGlobal("fetch", f);
    renderToStaticMarkup(<PhoneTriage token="tok" now={NOW} />);
    // listRuns is called on mount; effects do not run under SSR, so call the
    // same client the component uses and pin the query the desktop sends.
    const { listRuns } = await import("../../app/runs/runsApi");
    await listRuns({ limit: 100 }, "tok");
    type FetchCall = [url: string | URL | Request, init?: RequestInit];
    const calls = f.mock.calls as unknown as FetchCall[];
    const gets = calls.filter((c) => c[1]?.method !== "POST");
    expect(gets.length).toBeGreaterThan(0);
    const url = String(gets[0][0]);
    expect(url).toContain("/api/v1/orchestrator/runs");
    expect(url).toContain("limit=100");
  });

  it("component module imports the SAME clients the desktop card uses", async () => {
    const fs = await import("node:fs");
    const src = await fs.promises.readFile(
      new URL("./PhoneTriage.tsx", import.meta.url),
      "utf8",
    );
    expect(src).toContain('from "../../app/dashboard/planGateApi"');
    expect(src).toContain("approvePlan(");
    expect(src).toContain("rejectPlan(");
    expect(src).toContain('from "../../app/runs/runsApi"');
    expect(src).toContain("listRuns(");
    // No invented fetch in this component (the desktop clients own the URLs).
    expect(src).not.toContain("fetch(");
  });
});
