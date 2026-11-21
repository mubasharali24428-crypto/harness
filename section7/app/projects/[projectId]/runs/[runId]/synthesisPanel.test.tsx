// W28 S1b-2 — read-only synthesis panel. Node-env constraint (no jsdom/RTL
// in this repo): the real component is rendered with react-dom/server and the
// assertions run over emitted markup, the same protocol as controlPanels.test.
// Source-level parity checks cover what SSR cannot reach (the poll interval
// and the absence of any write path in the component's own source).
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { getSynthesis, type SynthesisView } from "./controlApi";
import { SynthesisPanel, type SynthesisState } from "./synthesisPanel";

afterEach(() => vi.unstubAllGlobals());

const HERE = dirname(fileURLToPath(import.meta.url));
const PANEL_SRC = readFileSync(join(HERE, "synthesisPanel.tsx"), "utf8");
const REVIEW_LINE = "Review required. Nothing is merged automatically.";

const childA: SynthesisView["children"][number] = {
  assignmentId: "a1", parentId: "p", childRunId: "r-a",
  title: "Write docs", state: "active", agentName: "coder", agentVersion: 3,
  delegatedBy: "eng", scope: { paths: ["src", "docs/readme.md"] },
  spentUsd: "0.002512", runStatus: "completed", result: "wrote src/a.txt",
};
const childB: SynthesisView["children"][number] = {
  ...childA, assignmentId: "a2", title: "Write tests", state: "cancelled",
  scope: { paths: ["src"] }, spentUsd: "0", runStatus: null, result: null,
};

function view(partial: Partial<SynthesisView> = {}): SynthesisView {
  return {
    runId: "r1", children: [childA, childB],
    conflicts: [{ assignmentIds: ["a1", "a2"], paths: ["src"] }],
    reviewRequired: true, autoMerge: false, ...partial,
  };
}

function html(state: SynthesisState): string {
  return renderToStaticMarkup(<SynthesisPanel state={state} />);
}

describe("synthesis panel — read-only review", () => {
  it("renders each child and the conflicts with the exact spend strings", () => {
    const h = html({ kind: "ready", view: view() });
    expect(h).toContain("Write docs");
    expect(h).toContain("Write tests");
    expect(h).toContain("coder v3");
    expect(h).toContain("eng");
    expect(h).toContain("src, docs/readme.md");
    expect(h).toContain("active");
    expect(h).toContain("cancelled");
    expect(h).toContain("completed");
    expect(h).toContain("wrote src/a.txt");
    expect(h).toContain('data-testid="synthesis-spend-a1">0.002512</td>');
    // A real "0" is data, not a missing value: shown as 0, never a dash.
    expect(h).toContain('data-testid="synthesis-spend-a2">0</td>');
    expect(h).not.toContain(">—</<");
    expect(h).toContain("Write docs and Write tests: src");
    expect(h).toContain('data-testid="synthesis-conflict-0"');
  });

  it("says 'no delegated children' on an empty successful response", () => {
    const h = html({ kind: "ready", view: view({ children: [], conflicts: [] }) });
    expect(h).toContain("no delegated children");
    expect(h).not.toContain('data-testid="synthesis-children"');
  });

  it("renders 'no result yet' for a null result, never a blank cell", () => {
    const h = html({ kind: "ready", view: view() });
    expect(h).toContain(">no result yet</td>");
    expect(h).not.toContain("></td>");
  });

  it("shows an explicit error state", () => {
    const h = html({ kind: "error", message: "offline" });
    expect(h).toContain('data-testid="synthesis-error"');
    expect(h).toContain("Could not load the synthesis: offline");
    expect(h).not.toContain('data-testid="synthesis-children"');
  });

  it("shows an explicit loading state", () => {
    const h = html({ kind: "loading" });
    expect(h).toContain('data-testid="synthesis-loading"');
    expect(h).toContain("Loading delegated children");
  });

  it("keeps the 'nothing is merged' line in every state", () => {
    const states: SynthesisState[] = [
      { kind: "loading" },
      { kind: "error", message: "offline" },
      { kind: "ready", view: view({ children: [], conflicts: [] }) },
      { kind: "ready", view: view() },
    ];
    for (const state of states) {
      expect(html(state)).toContain('data-testid="synthesis-review-line"');
      expect(html(state)).toContain(REVIEW_LINE);
    }
  });

  it("contains no button or form that can issue a write", () => {
    const h = html({ kind: "ready", view: view() });
    expect(h).not.toContain("<button");
    expect(h).not.toContain("<form");
    expect(h).not.toContain('type="submit"');
    expect(h).not.toContain("onSubmit");
    // Source parity: SSR strips handlers, so check the component itself too.
    expect(PANEL_SRC).not.toContain("<button");
    expect(PANEL_SRC).not.toContain("<form");
    expect(PANEL_SRC).not.toContain("fetch(");
    expect(PANEL_SRC).not.toContain("post(");
    expect(PANEL_SRC).not.toContain("method:");
    expect(PANEL_SRC).not.toContain("setInterval(load, 3000");
    // The only refresh is the agent-tree panel's 10 s interval, never faster.
    expect(PANEL_SRC).toContain("setInterval(load, 10000)");
  });
});

describe("getSynthesis uses the existing API helper", () => {
  it("GETs /runs/{runId}/synthesis with a bearer token and no body", async () => {
    const body = view({ children: [], conflicts: [] });
    const fn = vi.fn(async () => ({
      ok: true, status: 200, text: async () => JSON.stringify(body),
    }));
    vi.stubGlobal("fetch", fn);
    const got = await getSynthesis("r1", "tok");
    expect(got.children).toEqual([]);
    const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/api/v1/orchestrator/runs/r1/synthesis");
    expect(init.method).toBeUndefined();
    expect(init.body).toBeUndefined();
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer tok");
  });
});
