// Operability slice 2: run workspace, status first. Node-env vitest (no jsdom/RTL in this repo):
// the hook-free header is expanded with tests/support/elementTree and its button handler called;
// the step column is rendered with react-dom/server from an aggregate fixture. A "disconnected"
// fixture is the page's stale flag (the last refresh failed) with the last good aggregate.
import { describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { button, expand, findAll, hasButton, press, textOf } from "../../../tests/support/elementTree";
import type { RunAggregateDto } from "../../../app/runs/runsApi";
import { defaultDiffScope, showChangeReview } from "@/lib/diffScope";
import { RECONNECTING_TEXT, RunStatusHeader, headerNextAction, type RunStatusHeaderProps } from "./RunStatusHeader";
import { RunColumn, StepBlock, outcomeLabel } from "./RunColumn";
import type { RunView } from "./useRun";

const agg = (over: Partial<RunAggregateDto> = {}): RunAggregateDto => ({
  runId: "run-1", state: "executing", planId: "plan-1", planApproved: true, isHumanEditing: false, executing: true,
  engineMode: "live", costUsd: null, estimatedCostUsd: null, prId: null, url: null, budget: { degraded: false },
  steps: [
    { seq: 1, type: "plan_approved", ts: "2026-11-25T10:00:00Z" },
    { seq: 2, type: "tool_call", ts: "2026-11-25T10:01:00Z", detail: { callId: "c1", name: "bash", command: "npm test" } },
    { seq: 3, type: "tool_result", ts: "2026-11-25T10:01:05Z", detail: { callId: "c1", ok: false, error: "exit 1", durationMs: 5000 } },
    { seq: 4, type: "tool_call", ts: "2026-11-25T10:02:00Z", detail: { callId: "c2", name: "read_file", path: "src/a.ts" } },
    { seq: 5, type: "tool_result", ts: "2026-11-25T10:02:01Z", detail: { callId: "c2", ok: true, durationMs: 40 } },
  ],
  ...over,
});

const header = (over: Partial<RunStatusHeaderProps> = {}) => {
  const onOpenPane = vi.fn();
  const props: RunStatusHeaderProps = {
    goal: "Fix the login redirect", state: "executing", costUsd: null, startedAt: "2026-11-25T10:00:00Z",
    stale: false, onOpenPane, now: Date.parse("2026-11-25T10:30:00Z"), ...over,
  };
  return { onOpenPane, nodes: expand(React.createElement(RunStatusHeader, props)), html: renderToStaticMarkup(<RunStatusHeader {...props} />) };
};

const view = (a: RunAggregateDto, stale = false): RunView => ({
  status: "ready", error: null, runId: a.runId, agg: a, plan: null, review: null, reviewState: "absent", stale,
  retry: () => {}, refresh: async () => {},
});

describe("header: goal, state, spend or unknown", () => {
  it("leads with the goal as the h1, the state in words and spend", () => {
    const { nodes } = header({ costUsd: 12.5 });
    const [h1] = findAll(nodes, (n) => n.type === "h1");
    expect(textOf(h1)).toBe("Fix the login redirect");
    const text = nodes.map(textOf).join("");
    expect(text).toContain("State: ");
    expect(text).toContain("Spend $12.50");
  });

  it("null spend reads 'unknown', never $0", () => {
    const { html, nodes } = header({ costUsd: null });
    const [spend] = findAll(nodes, (n) => n.props["data-field"] === "spend");
    expect(textOf(spend)).toBe("Spend unknown");
    expect(html).not.toMatch(/Spend \$0/);
  });

  it("a missing start time is unknown, not a dash or an invented age", () => {
    expect(header({ startedAt: null }).nodes.map(textOf).join("")).toContain("Started unknown");
  });
});

describe("header: one next action, through the existing panes", () => {
  it("maps state to at most one action", () => {
    expect(headerNextAction("awaiting_plan_approval", false)).toEqual({ pane: "plan", label: "Open approval" });
    expect(headerNextAction("awaiting_plan_approval", true)).toBeNull();
    expect(headerNextAction("pr_opened")).toEqual({ pane: "diff", label: "Open diff" });
    expect(headerNextAction("done")).toEqual({ pane: "diff", label: "Open diff" });
    for (const s of ["executing", "planning", "paused", "blocked", "something_new"]) expect(headerNextAction(s)).toBeNull();
  });

  it("Open approval is a named button that opens the Plan pane", () => {
    const { nodes, onOpenPane } = header({ state: "awaiting_plan_approval", planApproved: false });
    const b = button(nodes, "Open approval");
    expect(b.props.type).toBe("button");
    press(b);
    expect(onOpenPane).toHaveBeenCalledWith("plan");
  });

  it("Open diff opens the Diff pane; a working run shows no next action", () => {
    const pr = header({ state: "pr_opened" });
    press(button(pr.nodes, "Open diff"));
    expect(pr.onOpenPane).toHaveBeenCalledWith("diff");
    expect(findAll(header({ state: "executing" }).nodes, (n) => n.props["data-next-action"])).toHaveLength(0);
    expect(hasButton(header({ state: "executing" }).nodes, /Open (diff|approval)/)).toBe(false);
  });
});

describe("timeline: each step is a receipt; failure is in the markup, not only colour", () => {
  it("outcome words per status", () => {
    expect(["failed", "ok", "running", "neutral"].map(outcomeLabel)).toEqual(["Failed", "Succeeded", "In progress", "Recorded"]);
  });

  it("a receipt says what, when and outcome", () => {
    const html = renderToStaticMarkup(
      <StepBlock label="Ran a command" receipt="npm test" ts="2026-11-25T10:01:00Z" status="failed" durationMs={5000} costNote={null} />,
    );
    expect(html).toMatch(/What<\/span> Ran a command: npm test/);
    expect(html).toMatch(/When<\/span> /);
    expect(html).toMatch(/Outcome<\/span> Failed/);
  });

  it("the failed tool call is marked failed in text and attributes; the successful one is not", () => {
    const html = renderToStaticMarkup(<RunColumn view={view(agg())} />);
    const items = html.split("<li").slice(1);
    const failed = items.filter((li) => li.includes('data-outcome="failed"'));
    expect(failed).toHaveLength(1);
    expect(failed[0]).toContain('class="ws-step-outcome" data-outcome="failed">Failed</span>');
    const ok = items.filter((li) => li.startsWith(' data-outcome="ok"'));
    expect(ok.length).toBeGreaterThan(0);
    for (const li of ok) expect(li).not.toContain(">Failed<");
  });
});

describe("disconnected: says Reconnecting and keeps the last state", () => {
  it("the stale header shows Reconnecting with the last goal, state and spend still on screen", () => {
    const { nodes } = header({ stale: true, costUsd: 3 });
    const notes = findAll(nodes, (n) => n.props["data-conn"] === "reconnecting");
    expect(notes).toHaveLength(1);
    expect(textOf(notes[0])).toBe(RECONNECTING_TEXT);
    expect(notes[0].props.role).toBe("status");
    const text = nodes.map(textOf).join("");
    expect(text).toContain("Fix the login redirect");
    expect(text).toContain("Spend $3.00");
  });

  it("the step column still renders the last fixture state while stale", () => {
    const live = renderToStaticMarkup(<RunColumn view={view(agg())} />);
    const stale = renderToStaticMarkup(<RunColumn view={view(agg(), true)} />);
    expect(stale).toBe(live);
    expect(stale.split("<li").length - 1).toBeGreaterThan(0);
  });

  it("a connected header has no Reconnecting note", () => {
    expect(header({ stale: false }).html).not.toContain("Reconnecting");
  });
});

describe("since-last stays the default diff (R4 regression guard, also in tests/unit/diffScope.test.ts)", () => {
  it("opens on since-last when an iteration exists and hides the snapshot history behind it", () => {
    expect(defaultDiffScope(true)).toBe("since-last");
    expect(showChangeReview("diff", "since-last")).toBe(false);
  });
});
