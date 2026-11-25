// Operability slice 3: approvals a non-expert can finish. Node-env vitest (no jsdom/RTL in this
// repo): the hook-free card is expanded with tests/support/elementTree and its buttons pressed;
// the decision call runs against a stubbed fetch and its result is fed back into the card, which
// is exactly what InlineApprovals does with it.
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { button, expand, findAll, press, textOf, type Node } from "../../../tests/support/elementTree";
import { PLAN_GATE_BASE } from "../../../app/dashboard/planGateApi";
import type { Approval } from "../../../app/projects/[projectId]/runs/[runId]/collabApi";
import { ApprovalCard, decide, decisionNames, plainOutcome, plainReason, type DecisionResult } from "./ApprovalCard";
import { cardsToShow } from "./RunPrompts";

afterEach(() => vi.unstubAllGlobals());

const approval = (over: Partial<Approval> = {}): Approval => ({
  id: "a1", runId: "r1", action: "delete", paths: ["src/old.py"], summary: "Remove the dead module\nIt has no importers.",
  diff: "-import x\n-def old(): ...", digest: "d".repeat(64), state: "pending", requester: "agent", decidedBy: null,
  decidedAt: null, outcome: null, expiresAt: "2026-12-01T00:00:00Z", ...over,
});

const card = (a: Approval, extra: { result?: DecisionResult | null; canAct?: boolean } = {}) => {
  const onDecide = vi.fn();
  const props = { approval: a, canAct: extra.canAct ?? true, busy: false, result: extra.result ?? null, onDecide };
  return { onDecide, nodes: expand(React.createElement(ApprovalCard, props)), html: renderToStaticMarkup(<ApprovalCard {...props} />) };
};
const field = (nodes: Node[], f: string) => findAll(nodes, (n) => n.props["data-field"] === f);

const stub = (status: number, body: unknown) =>
  vi.fn(async () => ({ ok: status >= 200 && status < 300, status, headers: { get: () => null }, text: async () => JSON.stringify(body) }));

describe("the card leads with a plain outcome and one line of why", () => {
  it("the heading says what approving does; the reason is one line", () => {
    const { nodes } = card(approval());
    const [h2] = findAll(nodes, (n) => n.type === "h2");
    expect(textOf(h2)).toBe("If you approve, the agent deletes src/old.py.");
    expect(textOf(field(nodes, "reason")[0])).toBe("Remove the dead module");
    expect(plainOutcome({ action: "modify", paths: ["a", "b", "c"] })).toBe("If you approve, the agent changes 3 files.");
  });

  it("prefers the server's plain sentence and never shows an empty reason", () => {
    expect(plainReason({ plain: "Deletes a file nothing imports.", summary: "x" })).toBe("Deletes a file nothing imports.");
    expect(plainReason({ summary: "   " })).toBe("The agent gave no reason.");
  });

  it("the file list and diff are one control away, in a closed details", () => {
    const { nodes } = card(approval());
    const [details] = findAll(nodes, (n) => n.type === "details");
    expect(details.props.open).toBeFalsy();
    expect(textOf(findAll([details], (n) => n.type === "summary")[0])).toBe("Technical details");
    expect(findAll([details], (n) => n.type === "pre" && n.props["aria-label"] === "Proposed change")).toHaveLength(1);
    expect(findAll([details], (n) => n.type === "code").map(textOf)).toEqual(["src/old.py"]);
    // nothing technical sits above the fold
    const before = nodes.flatMap((n) => n.children).slice(0, 2).map(textOf).join("");
    expect(before).not.toContain("-import x");
  });
});

describe("similarCases is read-only text (CD-02)", () => {
  it("lists earlier cases as text with no control that applies a rule", () => {
    const a = approval({ similarCases: [
      { caseId: "c1", runId: "old-run", outcome: "denied 3 times", score: 0.9, suggestedRule: "Allow edits under src/legacy/" },
    ] });
    const { nodes } = card(a);
    const [similar] = field(nodes, "similar-cases");
    expect(textOf(similar)).toContain("Allow edits under src/legacy/");
    expect(findAll([similar], (n) => ["button", "a", "input", "form"].includes(n.type))).toHaveLength(0);
    const all = findAll(nodes, (n) => n.type === "button").map((b) => `${textOf(b)} ${b.props["aria-label"] ?? ""}`);
    for (const label of all) expect(label).not.toMatch(/rule|save|apply/i);
  });
});

describe("Approve and Deny", () => {
  it("have distinct accessible names that name the change, and are native buttons", () => {
    const { nodes } = card(approval());
    const approve = button(nodes, "Approve deleting src/old.py");
    const deny = button(nodes, "Deny deleting src/old.py");
    expect(approve.props["aria-label"]).not.toBe(deny.props["aria-label"]);
    expect(approve.props.type).toBe("button");
    expect(textOf(approve)).toBe("Approve");
    expect(textOf(deny)).toBe("Deny");
    expect(decisionNames({ action: "modify", paths: ["a", "b"] })).toEqual({ approve: "Approve changing 2 files", deny: "Deny changing 2 files" });
  });

  it("call back with the approval and the decision; nothing decides on render", () => {
    const { nodes, onDecide } = card(approval());
    expect(onDecide).not.toHaveBeenCalled();
    press(button(nodes, /^Approve deleting/));
    press(button(nodes, /^Deny deleting/));
    expect(onDecide.mock.calls.map((c) => [c[0].id, c[1]])).toEqual([["a1", true], ["a1", false]]);
  });

  it("are disabled with a reason for a role that cannot decide", () => {
    const { nodes, html } = card(approval(), { canAct: false });
    expect(button(nodes, /^Approve/).props.disabled).toBe(true);
    expect(button(nodes, /^Deny/).props.disabled).toBe(true);
    expect(html).toContain("Only admins and engineers can decide.");
  });
});

describe("after a mocked decision", () => {
  it("success: POSTs the decision with the digest, then the card shows a receipt instead of the buttons", async () => {
    const f = stub(200, approval({ state: "approved", decidedBy: "lead", decidedAt: "2026-11-25T10:00:00Z" }));
    vi.stubGlobal("fetch", f);
    const result = await decide("r1", "tok", approval(), true);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${PLAN_GATE_BASE}/api/v1/orchestrator/runs/r1/approvals/a1/decision`);
    expect(JSON.parse(String(init.body))).toEqual({ approve: true, digest: "d".repeat(64) });

    const { nodes } = card(approval(), { result });
    const [receipt] = field(nodes, "receipt");
    expect(receipt.props.role).toBe("status");
    expect(textOf(receipt)).toMatch(/^Approved by lead on /);
    expect(findAll(nodes, (n) => n.type === "button")).toHaveLength(0);
  });

  it("a denial receipt says Denied; an approved staged change says applying is a separate step", async () => {
    vi.stubGlobal("fetch", stub(200, approval({ state: "rejected", decidedBy: "lead" })));
    const denied = await decide("r1", "tok", approval(), false);
    expect(textOf(field(card(approval(), { result: denied }).nodes, "receipt")[0])).toBe("Denied by lead.");
    vi.stubGlobal("fetch", stub(200, approval({ state: "approved", requester: "system:staging", decidedBy: "lead" })));
    const staged = await decide("r1", "tok", approval({ requester: "system:staging" }), true);
    expect(textOf(field(card(approval(), { result: staged }).nodes, "receipt")[0])).toMatch(/not applied yet/);
  });

  it("error: the error text stays on the card, and the buttons stay so the person can retry", async () => {
    vi.stubGlobal("fetch", stub(409, { error: { code: "digest_mismatch", message: "The proposal changed; reload it." } }));
    const result = await decide("r1", "tok", approval(), true);
    expect(result.ok).toBe(false);
    const { nodes } = card(approval(), { result });
    const [err] = field(nodes, "error");
    expect(err.props.role).toBe("alert");
    expect(textOf(err)).toContain("Approve did not go through");
    expect(textOf(err)).toContain("The proposal changed; reload it.");
    expect(button(nodes, /^Approve deleting/)).toBeTruthy();
    expect(field(nodes, "receipt")).toHaveLength(0);
  });

  it("a decided card keeps its receipt after the pending list drops it; a failed one stays with its error", () => {
    const ok: DecisionResult = { ok: true, approve: true, approval: approval({ state: "approved" }) };
    const bad: DecisionResult = { ok: false, approve: false, error: "boom" };
    const b = approval({ id: "b2" });
    const shown = cardsToShow([b], { a1: { approval: approval(), result: ok }, b2: { approval: b, result: bad } });
    expect(shown.map((c) => [c.approval.id, c.result?.ok])).toEqual([["b2", false], ["a1", true]]);
    // a failed decision on a card the server no longer lists is not resurrected
    expect(cardsToShow([], { b2: { approval: b, result: bad } })).toEqual([]);
  });
});
