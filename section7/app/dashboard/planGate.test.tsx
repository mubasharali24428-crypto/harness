// W1-C2 — PlanGate component tests: state-driven rendering matrix.
// Node-env constraint (no jsdom/RTL in this repo, and installs are out of
// scope for this lane): components are rendered with react-dom/server's real
// React renderer and asserted on the emitted markup. Interaction gating logic
// itself lives in pure functions covered by planGateApi.test.ts.
import { describe, expect, it } from "vitest";
// Classic JSX transform under vitest/esbuild: React must be in scope here AND
// in planGate.tsx (harmless for Next builds, which accept explicit imports).
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { RunStatus } from "@contracts/api";
import type { Principal } from "@contracts/roles";
import { PlanGateCard, PlanGateView } from "./planGate";
import { effectivePhase, OPTIMISTIC_PENDING_CAPTION } from "./planGate";

function status(partial: Partial<RunStatus>): RunStatus {
  return {
    runId: partial.runId ?? "run-x",
    state: partial.state ?? "idle",
    planId: partial.planId ?? null,
    planApproved: partial.planApproved ?? false,
    isHumanEditing: partial.isHumanEditing ?? false,
  };
}

const noopActions = {
  runId: null,
  propose: () => {},
  approve: () => {},
  reject: () => {},
  start: () => {},
};

/** Order-agnostic: pull the <button …> open tag carrying a given testid
 *  (React SSR attribute order is not guaranteed). */
function buttonTag(html: string, testid: string): string {
  const tags = html.match(/<button\b[^>]*>/g) ?? [];
  return tags.find((t) => t.includes(`data-testid="${testid}"`)) ?? "";
}

function view(opts: {
  phase: Parameters<typeof PlanGateView>[0]["phase"];
  role?: string | null;
  busy?: boolean;
  goalDraft?: string;
  note?: string | null;
  error?: string | null;
  optimisticPending?: boolean;
  modelPicker?: React.ReactNode;
}) {
  const allOn = { propose: true, approve: true, reject: true, start: true };
  const allOff = { propose: false, approve: false, reject: false, start: false };
  return renderToStaticMarkup(
    <PlanGateView
      phase={opts.phase}
      enabled={
        opts.role === "viewer" || opts.role === "guest" || opts.busy ? allOff : allOn
      }
      plan={opts.goalDraft ? { goal: opts.goalDraft } : null}
      goalDraft={opts.goalDraft ?? ""}
      onGoalDraftChange={() => {}}
      busyAction={opts.busy ? "approve" : null}
      error={opts.error ?? null}
      note={opts.note ?? null}
      actions={noopActions}
      optimisticPending={opts.optimisticPending ?? false}
      modelPicker={opts.modelPicker}
    />,
  );
}

describe("PlanGateView — one action set per state", () => {
  it("no-plan: goal input + Propose only", () => {
    const html = view({ phase: "no-plan" });
    expect(html).toContain('aria-label="Run goal"');
    expect(html).toContain('data-testid="propose-plan"');
    expect(html).not.toContain('data-testid="approve-plan"');
    expect(html).not.toContain('data-testid="reject-plan"');
    expect(html).not.toContain('data-testid="start-run"');
    expect(html).toContain('data-testid="plan-gate-phase">no-plan<');
  });

  it("awaiting-approval: Approve + Reject, no propose form, no start", () => {
    const html = view({ phase: "awaiting-approval", goalDraft: "g" });
    expect(html).toContain('data-testid="approve-plan"');
    expect(html).toContain('data-testid="reject-plan"');
    expect(html).not.toContain('data-testid="start-run"');
    expect(html).not.toContain('aria-label="Run goal"');
  });

  it("ready-to-start: Start only", () => {
    const html = view({ phase: "ready-to-start", goalDraft: "g" });
    expect(html).toContain('data-testid="start-run"');
    expect(html).not.toContain('data-testid="approve-plan"');
    expect(html).not.toContain('data-testid="reject-plan"');
    expect(html).not.toContain('data-testid="propose-plan"');
  });

  it("in-flight and done: gate closed, no action buttons at all", () => {
    for (const phase of ["in-flight", "done"] as const) {
      const html = view({ phase });
      expect(html).not.toContain('data-testid="propose-plan"');
      expect(html).not.toContain('data-testid="approve-plan"');
      expect(html).not.toContain('data-testid="reject-plan"');
      expect(html).not.toContain('data-testid="start-run"');
      expect(html).toContain("No plan-gate action available");
    }
  });

  it("viewer/guest render disabled buttons — never hidden affordances they cannot use", () => {
    // Enabled-map off ⇒ every rendered action carries the disabled attribute.
    const html = view({ phase: "awaiting-approval", role: "viewer", goalDraft: "g" });
    expect(buttonTag(html, "approve-plan")).toContain("disabled");
    expect(buttonTag(html, "reject-plan")).toContain("disabled");
  });

  it("busy disables the in-progress action and flips its label", () => {
    const html = view({ phase: "awaiting-approval", busy: true, goalDraft: "g" });
    expect(buttonTag(html, "approve-plan")).toContain("disabled");
    expect(html).toContain("Approving…");
  });

  it("empty goal keeps Propose disabled (no empty-goal proposes)", () => {
    const html = renderToStaticMarkup(
      <PlanGateView
        phase="no-plan"
        enabled={{ propose: true, approve: false, reject: false, start: false }}
        plan={null}
        goalDraft="   "
        onGoalDraftChange={() => {}}
        busyAction={null}
        error={null}
        note={null}
        actions={noopActions}
      />,
    );
    expect(buttonTag(html, "propose-plan")).toContain("disabled");
  });

  it("renders note as status and error as alert", () => {
    const html = view({
      phase: "awaiting-approval",
      goalDraft: "g",
      note: "Plan proposed — awaiting approval.",
      error: "ROLE_FORBIDDEN",
    });
    expect(html).toContain('role="status"');
    expect(html).toContain('role="alert"');
  });
});

describe("PlanGateCard — connected card derives phase from RunStatus.state", () => {
  const admin: Principal = { userId: "u1", teamId: "t1", role: "admin" };

  it("idle + unapproved snapshot ⇒ proposing phase", () => {
    const html = renderToStaticMarkup(
      <PlanGateCard token="tok" principal={admin} status={status({ state: "idle" })} />,
    );
    expect(html).toContain('data-testid="plan-gate-phase">no-plan<');
    expect(html).toContain('data-testid="propose-plan"');
  });

  it("idle + approved snapshot ⇒ ready-to-start with server run binding shown", () => {
    const html = renderToStaticMarkup(
      <PlanGateCard
        token="tok"
        principal={admin}
        status={status({ state: "idle", planApproved: true })}
        runId="run-from-server"
      />,
    );
    expect(html).toContain('data-testid="plan-gate-phase">ready-to-start<');
    expect(html).toContain('data-testid="start-run"');
    expect(html).toContain("run-from-server");
  });

  it("executing ⇒ closed gate even for admins", () => {
    const html = renderToStaticMarkup(
      <PlanGateCard
        token="tok"
        principal={admin}
        status={status({ state: "executing", planApproved: true, planId: "p1" })}
      />,
    );
    expect(html).toContain('data-testid="plan-gate-phase">in-flight<');
    expect(html).not.toContain('data-testid="start-run"');
  });

  it("done ⇒ terminal caption", () => {
    const html = renderToStaticMarkup(
      <PlanGateCard
        token="tok"
        principal={admin}
        status={status({ state: "done", planApproved: true })}
      />,
    );
    expect(html).toContain('data-testid="plan-gate-phase">done<');
  });

  it("no status yet ⇒ honest no-plan state (nothing invented)", () => {
    const html = renderToStaticMarkup(
      <PlanGateCard token="tok" principal={admin} status={null} />,
    );
    expect(html).toContain('data-testid="plan-gate-phase">no-plan<');
  });

  it("viewer principal sees disabled actions in every active phase", () => {
    const viewer: Principal = { userId: "u2", teamId: "t1", role: "viewer" };
    const html = renderToStaticMarkup(
      <PlanGateCard
        token="tok"
        principal={viewer}
        status={status({ state: "awaiting_plan_approval", planId: "p9" })}
      />,
    );
    expect(html).toContain('data-testid="plan-gate-phase">awaiting-approval<');
    expect(buttonTag(html, "approve-plan")).toContain("disabled");
    expect(buttonTag(html, "reject-plan")).toContain("disabled");
  });
});

// M14-F4 — optimistic phase overlay + pending affordances.
describe("effectivePhase — optimistic guess over server truth", () => {
  it("renders the optimistic guess while one is set", () => {
    expect(effectivePhase("awaiting-approval", "ready-to-start")).toBe("ready-to-start");
    expect(effectivePhase("no-plan", "awaiting-approval")).toBe("awaiting-approval");
  });

  it("falls back to the last server truth once the overlay clears", () => {
    expect(effectivePhase("awaiting-approval", null)).toBe("awaiting-approval");
    expect(effectivePhase("done", null)).toBe("done");
  });
});

describe("PlanGateView — optimistic decision rendering", () => {
  it("approve-in-flight shows ready-to-start pill + Start affordance immediately", () => {
    const html = view({
      phase: "ready-to-start", // optimistic guess over a stale awaiting snapshot
      goalDraft: "g",
      busy: true,
      optimisticPending: true,
    });
    expect(html).toContain('data-testid="plan-gate-phase">ready-to-start<');
    expect(html).toContain('data-testid="start-run"'); // next action teased instantly
    expect(html).toContain(OPTIMISTIC_PENDING_CAPTION); // wire call visibly in flight
  });

  it("reject keeps its affordance visible-but-pending until the 202 confirms", () => {
    const html = view({
      phase: "awaiting-approval", // card STAYS — S4 returns rejected runs to idle
      goalDraft: "g",
      busy: true,
      optimisticPending: true,
    });
    expect(html).toContain('data-testid="reject-plan"'); // never silently vanishes
    expect(buttonTag(html, "reject-plan")).toContain("disabled");
    expect(buttonTag(html, "approve-plan")).toContain("disabled");
    expect(html).toContain(OPTIMISTIC_PENDING_CAPTION);
  });

  it("cleared overlay (rollback/confirm) restores the plain caption", () => {
    const html = view({ phase: "awaiting-approval", goalDraft: "g" });
    expect(html).not.toContain(OPTIMISTIC_PENDING_CAPTION);
    expect(html).toContain("Plan awaiting approval.");
    expect(html).toContain("Approve plan"); // normal labels back
  });
});

describe("PlanGateView — composer model picker slot", () => {
  it("renders the picker beside the shortcut hint in the propose footer", () => {
    const html = view({
      phase: "no-plan",
      modelPicker: <span data-testid="stub-picker">deepseek-reasoner</span>,
    });
    expect(html).toContain('data-testid="stub-picker"');
    expect(html).toContain("⌘↵ to propose");
    // Inside the composer: the picker precedes the hint in the same footer row.
    expect(html.indexOf('data-testid="stub-picker"')).toBeLessThan(html.indexOf("⌘↵ to propose"));
  });

  it("keeps the gate usable when no picker is supplied (backward compatible)", () => {
    const html = view({ phase: "no-plan" });
    expect(html).toContain('data-testid="propose-plan"');
    expect(html).not.toContain('data-testid="stub-picker"');
  });
});
