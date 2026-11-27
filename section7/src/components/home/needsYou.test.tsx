// Operability slice 1: Home "Needs you". Node-env vitest (no jsdom/RTL in this repo, installs are
// out of scope): the hook-free view is expanded with tests/support/elementTree and its handlers are
// called with fake events; the loader is driven with a stubbed fetch. This proves the rows, the
// three states, the accessible names and the Enter path. It does NOT prove real focus or timing
// (the U1 "under 10 s on a real inbox" check is owner-Mac, NOT RUN here).
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) =>
    React.createElement("a", { href, ...rest }, children),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { button, expand, findAll, press, textOf, type Node } from "../../../tests/support/elementTree";
import { ORCHESTRATOR_BASE } from "@/lib/api";
import type { RunDto } from "../../../app/runs/runsApi";
import { NEEDS_YOU_CALL, listKeyTarget, needsYouItems, needsYouReason, shouldFocusList } from "./needsYou";
import { NEEDS_YOU_LIST_ID, NeedsYouView, loadNeedsYou, type NeedsYouState } from "./NeedsYouList";

afterEach(() => vi.unstubAllGlobals());

const NOW = Date.parse("2026-11-25T12:00:00Z");
const run = (over: Partial<RunDto>): RunDto => ({
  id: "r", projectId: "p1", status: "done", createdAt: "2026-11-25T11:00:00Z", startedAt: null, finishedAt: null,
  goal: null, planId: null, planApproved: false, blockedBy: null, ...over,
});

const FIXTURE: RunDto[] = [
  run({ id: "working", status: "executing", goal: "Refactor auth" }),
  run({ id: "rev", status: "pr_opened", goal: "Add rate limits", createdAt: "2026-11-25T11:50:00Z" }),
  run({ id: "appr", status: "awaiting_plan_approval", goal: "Fix login bug", createdAt: "2026-11-25T09:00:00Z" }),
  run({ id: "blk", status: "blocked", blockedBy: "question", goal: "Upgrade deps", createdAt: "2026-11-25T10:30:00Z" }),
  run({ id: "finished", status: "done", goal: "Old" }),
];

const tree = (state: NeedsYouState, onOpen = vi.fn(), onRetry = vi.fn()) =>
  ({ nodes: expand(React.createElement(NeedsYouView, { state, onOpen, onRetry })), onOpen, onRetry });
const links = (nodes: Node[]) => findAll(nodes, (n) => n.type === "a");

describe("needsYouItems: one list of what waits on a person", () => {
  it("keeps approvals, blocked runs and open reviews, oldest wait first; drops working and done runs", () => {
    const items = needsYouItems(FIXTURE, NOW);
    expect(items.map((i) => [i.id, i.kind])).toEqual([["appr", "approval"], ["blk", "blocked"], ["rev", "review"]]);
    expect(items.map((i) => i.age)).toEqual(["3h ago", "1h ago", "10m ago"]);
  });

  it("each row has a one-line plain reason; an unknown blocker says it is unknown", () => {
    expect(needsYouReason({ status: "awaiting_plan_approval", planApproved: false })).toBe("Plan is waiting for your approval");
    expect(needsYouReason({ status: "awaiting_plan_approval", planApproved: true })).toMatch(/waiting for someone to start it/);
    expect(needsYouReason({ status: "blocked", blockedBy: "question" })).toMatch(/asked a question/);
    expect(needsYouReason({ status: "blocked", blockedBy: "something_new" })).toMatch(/reason was not reported/);
    expect(needsYouReason({ status: "pr_opened" })).toMatch(/waiting for review/);
    for (const i of needsYouItems(FIXTURE, NOW)) expect(i.reason).not.toMatch(/\n/);
  });

  it("opens approvals on the plan tab and other kinds on the run page (existing routes)", () => {
    const byId = Object.fromEntries(needsYouItems(FIXTURE, NOW).map((i) => [i.id, i.href]));
    expect(byId).toEqual({ appr: "/projects/p1/runs/appr?tab=plan", blk: "/projects/p1/runs/blk", rev: "/projects/p1/runs/rev" });
  });

  it("a missing time is unknown, never invented", () => {
    const [i] = needsYouItems([run({ id: "x", status: "blocked", createdAt: "" as unknown as string })], NOW);
    expect(i.age).toBe("—");
  });
});

describe("NeedsYouView: ready, empty and error states", () => {
  it("renders every row with age, reason and exactly one primary action, Open, with a distinct accessible name", () => {
    const { nodes } = tree({ status: "ready", items: needsYouItems(FIXTURE, NOW) });
    const rows = findAll(nodes, (n) => n.type === "li");
    expect(rows).toHaveLength(3);
    for (const r of rows) {
      const actions = findAll([r], (n) => n.type === "a" || n.type === "button");
      expect(actions).toHaveLength(1);
      expect(textOf(actions[0])).toBe("Open");
      expect(findAll([r], (n) => n.type === "time")).toHaveLength(1);
    }
    const names = links(nodes).map((a) => a.props["aria-label"]);
    expect(names[0]).toBe("Open Fix login bug: Plan is waiting for your approval");
    expect(new Set(names).size).toBe(names.length);
    expect(textOf(rows[1])).toContain("Blocked:");
  });

  it("empty says Nothing waiting and links to Runs; it has no Retry", () => {
    const { nodes } = tree({ status: "ready", items: [] });
    const text = nodes.map(textOf).join("");
    expect(text).toContain("Nothing waiting");
    expect(links(nodes).map((a) => a.props.href)).toEqual(["/runs"]);
    expect(findAll(nodes, (n) => n.type === "button")).toHaveLength(0);
  });

  it("error names the failed call, keeps the reason, is an alert, and Retry calls back", () => {
    const { nodes, onRetry } = tree({ status: "error", call: NEEDS_YOU_CALL, message: "Server said 503." });
    const alert = findAll(nodes, (n) => n.props.role === "alert");
    expect(alert).toHaveLength(1);
    expect(textOf(alert[0])).toContain("GET /api/v1/orchestrator/runs");
    expect(textOf(alert[0])).toContain("Server said 503.");
    expect(textOf(alert[0])).not.toContain("Nothing waiting");
    press(button(nodes, "Retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("loading is a status region, not an empty list", () => {
    const html = renderToStaticMarkup(React.createElement(NeedsYouView, { state: { status: "loading" }, onOpen: () => {}, onRetry: () => {} }));
    expect(html).toContain('role="status"');
    expect(html).not.toContain("Nothing waiting");
  });
});

describe("keyboard: the go-to shortcut focuses the list, Enter opens the first row", () => {
  it("the list is a named, programmatically focusable list (tabindex -1) with the id the page focuses", () => {
    const { nodes } = tree({ status: "ready", items: needsYouItems(FIXTURE, NOW) });
    const [ul] = findAll(nodes, (n) => n.type === "ul");
    expect(ul.props.id).toBe(NEEDS_YOU_LIST_ID);
    expect(ul.props.tabIndex).toBe(-1);
    expect(ul.props["aria-labelledby"]).toBe("needs-you-heading");
  });

  it("Enter on the focused list opens the oldest waiting item", () => {
    const { nodes, onOpen } = tree({ status: "ready", items: needsYouItems(FIXTURE, NOW) });
    const [ul] = findAll(nodes, (n) => n.type === "ul");
    const seen = press(ul, "onKeyDown", { key: "Enter" });
    expect(onOpen).toHaveBeenCalledWith("/projects/p1/runs/appr?tab=plan");
    expect(seen.prevented).toBe(true);
  });

  it("Enter on a row link is left to the link; other keys and modified Enter do nothing", () => {
    const items = needsYouItems(FIXTURE, NOW);
    const list = {};
    expect(listKeyTarget({ key: "Enter", target: {}, currentTarget: list }, items)).toBeNull();
    expect(listKeyTarget({ key: "ArrowDown", target: list, currentTarget: list }, items)).toBeNull();
    expect(listKeyTarget({ key: "Enter", ctrlKey: true, target: list, currentTarget: list }, items)).toBeNull();
    expect(listKeyTarget({ key: "Enter", target: list, currentTarget: list }, [])).toBeNull();
  });

  it("the list takes focus only when the page was just reached, never from an input someone is typing in", () => {
    expect(shouldFocusList({ tagName: "BODY" }, "main-content")).toBe(true);
    expect(shouldFocusList({ tagName: "MAIN", id: "main-content" }, "main-content")).toBe(true);
    expect(shouldFocusList({ tagName: "TEXTAREA", id: "composer" }, "main-content")).toBe(false);
    expect(shouldFocusList({ tagName: "INPUT" }, "main-content")).toBe(false);
  });
});

describe("loadNeedsYou against a mocked runs list", () => {
  const stub = (status: number, body: unknown) =>
    vi.fn(async () => ({ ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body) }));

  it("GETs the runs list with the bearer and returns ready rows", async () => {
    const f = stub(200, { items: FIXTURE, nextCursor: null });
    vi.stubGlobal("fetch", f);
    const out = await loadNeedsYou("tok", NOW);
    expect(out.status).toBe("ready");
    expect(out.status === "ready" && out.items.map((i) => i.id)).toEqual(["appr", "blk", "rev"]);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${ORCHESTRATOR_BASE}/api/v1/orchestrator/runs?limit=100`);
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer tok");
  });

  it("a failed call is an error naming the call, never an empty 'Nothing waiting'", async () => {
    vi.stubGlobal("fetch", stub(500, { error: { code: "internal", message: "db down" } }));
    const out = await loadNeedsYou("tok", NOW);
    expect(out.status).toBe("error");
    expect(out.status === "error" && out.call).toBe(NEEDS_YOU_CALL);
  });
});
