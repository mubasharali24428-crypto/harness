// Multiplayer pilot S3 — model, client and static-render tests (node env, no jsdom).
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError, parseApiError } from "@/lib/api";
import { decideApproval, sendRedirect, type Approval, type CollabEvent, type Roster } from "./collabApi";
import {
  approvalLabel, clipDiff, deriveCollabView, describeDecision, describeEvent, fileStatuses, mergeEvents,
  redirectProblem, reopenedApprovals,
} from "./collabModel";
import { NotEnabled, PILOT_NOT_ENABLED, SharedRunView } from "./sharedRunPanel";

afterEach(() => vi.unstubAllGlobals());

const roster: Roster = {
  participants: [
    { userId: "lead", role: "leader" },
    { userId: "eng2", role: "participant" },
    { userId: "view", role: "observer" },
  ],
  leader: "lead",
  pendingHandoff: null,
};
const approval = (over: Partial<Approval> = {}): Approval => ({
  id: "a1", runId: "r1", action: "delete", paths: ["src/old.py"], summary: "remove dead module",
  diff: "-import x", digest: "d".repeat(64), state: "pending", requester: "eng2",
  decidedBy: null, outcome: null, expiresAt: "2026-10-18T00:00:00Z", ...over,
});

describe("deriveCollabView", () => {
  it("leader sees handoff targets and can decide; blocked reason names them", () => {
    const v = deriveCollabView(roster, [approval()], "lead", true);
    expect(v.isLeader && v.canRedirect).toBe(true);
    expect(v.handoffTargets).toEqual(["eng2"]);
    expect(v.blockedReason).toBe("Execution is blocked until you decide 1 pending request.");
    const other = deriveCollabView(roster, [approval(), approval({ id: "a2" })], "eng2", true);
    expect(other.blockedReason).toBe("Execution is blocked until lead decides 2 pending requests.");
  });
  it("observer cannot steer; non-member is not joined", () => {
    expect(deriveCollabView(roster, [], "view", false).canRedirect).toBe(false);
    const v = deriveCollabView(roster, [], "stranger", true);
    expect(v.joined).toBe(false);
    expect(v.canRedirect).toBe(false);
  });
  it("leaderless run offers leadership only to engineers/admins", () => {
    const r = { participants: [], leader: null, pendingHandoff: null };
    expect(deriveCollabView(r, [], "eng2", true).canClaimLeader).toBe(true);
    expect(deriveCollabView(r, [], "view", false).canClaimLeader).toBe(false);
  });
  it("pending handoff is surfaced to recipient and proposer, not both as actionable", () => {
    const r = { ...roster, pendingHandoff: { id: "h1", from: "lead", to: "eng2" } };
    expect(deriveCollabView(r, [], "eng2", true).handoffForMe).toEqual({ id: "h1", from: "lead" });
    const lead = deriveCollabView(r, [], "lead", true);
    expect(lead.handoffFromMe).toEqual({ id: "h1", to: "eng2" });
    expect(lead.handoffTargets).toEqual([]);
  });
});

describe("helpers", () => {
  it("scope/permission redirects need a reason, guidance does not", () => {
    expect(redirectProblem("guidance", "go", "")).toBeNull();
    expect(redirectProblem("scope_change", "go", " ")).toMatch(/reason/i);
    expect(redirectProblem("guidance", "  ", "")).toMatch(/first/i);
  });
  it("describes events without exposing raw JSON", () => {
    expect(describeEvent({ seq: 1, actor: "eng2", kind: "redirect.guidance", payload: { content: "use v2" }, ts: "" }))
      .toBe("eng2 gave guidance: use v2");
  });
  it("clips long diffs", () => {
    expect(clipDiff("x".repeat(10), 4)).toContain("6 more characters");
  });
});

describe("client", () => {
  function stub(body: unknown, status = 200) {
    const fn = vi.fn(async () => ({ ok: status < 400, status, text: async () => JSON.stringify(body) }));
    vi.stubGlobal("fetch", fn);
    return fn;
  }
  it("redirect sends only content fields (identity is the bearer token)", async () => {
    const fn = stub({ seq: 3, duplicate: false, invalidatedApprovals: 0 });
    await sendRedirect("r 1", "tok", { kind: "guidance", content: "hi", requestId: "q" });
    const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/runs/r%201/redirect");
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer tok");
    expect(Object.keys(JSON.parse(init.body as string)).sort()).toEqual(["content", "kind", "requestId"]);
  });
  it("approval decision echoes the reviewed digest; stale digest surfaces as ApiError", async () => {
    const fn = stub({ error: { code: "DIGEST_MISMATCH", message: "proposal changed since you reviewed it" } }, 409);
    await expect(decideApproval("r1", "tok", approval(), true)).rejects.toBeInstanceOf(ApiError);
    const init = (fn.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(JSON.parse(init.body as string)).toEqual({ approve: true, digest: "d".repeat(64) });
  });
});

function render(me: string, approvals: Approval[], r: Roster = roster, error: string | null = null) {
  const view = deriveCollabView(r, approvals, me, me !== "view");
  const noop = () => undefined;
  return renderToStaticMarkup(
    <SharedRunView me={me} roster={r} view={view} approvals={approvals} events={[]} error={error}
      busy={false} onApply={noop} onJoin={noop} onClaim={noop} onDecide={noop} onHandoff={noop}
      onHandoffDecision={noop} onRedirect={noop} />,
  );
}

describe("SharedRunView", () => {
  it("leader sees approve/reject with the diff, blocked banner and leader line", () => {
    const html = render("lead", [approval()]);
    expect(html).toContain("Approve this change");
    expect(html).toContain("-import x");
    expect(html).toContain('data-testid="blocked-banner"');
    expect(html).toContain("(you)");
    expect(html).toContain("Led by");
  });
  it("non-leader sees who must decide and no approve button", () => {
    const html = render("eng2", [approval()]);
    expect(html).not.toContain("Approve this change");
    expect(html).toContain("Only lead can decide this.");
  });
  it("observer gets no composer; unknown viewer is told to join", () => {
    expect(render("view", [])).toContain("Observers can follow along");
    const html = render("stranger", [], roster);
    expect(html).toContain("Join this run");
    expect(html).toContain("Join the run to steer it.");
  });
  it("only the leader may pick scope/permission directions; guidance is open", () => {
    const eng = render("eng2", []);
    expect(eng).toMatch(/<option value="scope_change" disabled/);
    const lead = render("lead", []);
    expect(lead).not.toMatch(/<option value="scope_change" disabled/);
  });
  it("voided approvals are labelled honestly and have no actions; errors are announced", () => {
    const html = render("lead", [approval({ state: "invalidated" })], roster, "boom");
    expect(html).toContain("Voided");
    expect(html).not.toContain("Approve this change");
    expect(html).toContain('role="alert"');
  });
  it("escapes hostile summary text", () => {
    const html = render("lead", [approval({ summary: "<img src=x onerror=alert(1)>" })]);
    expect(html).not.toContain("<img src=x");
  });
});

describe("staged approval application", () => {
  it("keeps approved staged changes blocked and offers an explicit Apply action", () => {
    const a = approval({ state: "approved", requester: "system:staging" });
    expect(deriveCollabView(roster, [a], "lead", true).blockedReason).toMatch(/appl/i);
    expect(render("lead", [a])).toContain("Apply approved change");
    expect(render("eng2", [a])).not.toContain("Apply approved change");
  });
});


describe("pilot flag", () => {
  it("parses the server code the panel keys on and renders a plain notice", () => {
    const { code } = parseApiError(404, { detail: { error: { code: "PILOT_NOT_ENABLED", message: "x" } } });
    expect(code).toBe(PILOT_NOT_ENABLED);
    const html = renderToStaticMarkup(React.createElement(NotEnabled));
    expect(html).toContain("not enabled for your team");
    expect(html).toContain('data-testid="shared-run-not-enabled"');
  });
});


// ---- parts 12/24: responsible person, file status, history, a11y, no private reasoning ----

function renderWith(me: string, approvals: Approval[], opts: { roster?: Roster; events?: CollabEvent[]; reopened?: string[]; goal?: string } = {}) {
  const r = opts.roster ?? roster;
  const view = deriveCollabView(r, approvals, me, me !== "view");
  const noop = () => undefined;
  return renderToStaticMarkup(
    <SharedRunView me={me} roster={r} view={view} approvals={approvals} events={opts.events ?? []} error={null}
      busy={false} reopened={opts.reopened} goal={opts.goal} onApply={noop} onJoin={noop} onClaim={noop} onDecide={noop}
      onHandoff={noop} onHandoffDecision={noop} onRedirect={noop} />,
  );
}
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("goal and responsible person", () => {
  it("shows the server-recorded goal above the leader line, and nothing when unknown", () => {
    const html = renderWith("lead", [], { goal: "Tidy the docs" });
    expect(html).toContain('data-testid="goal-line"');
    expect(html.indexOf("goal-line")).toBeLessThan(html.indexOf("leader-line"));
    expect(renderWith("lead", [])).not.toContain("goal-line");
  });
});

describe("pending decisions name the responsible person", () => {
  it("leader sees themselves, a teammate sees the leader, a leaderless run says nobody can decide", () => {
    expect(text(renderWith("lead", [approval()]))).toMatch(/Decision needed from lead \(you\) ?\./);
    expect(text(renderWith("eng2", [approval()]))).toMatch(/Decision needed from lead ?\./);
    const none: Roster = { participants: [{ userId: "eng2", role: "participant" }], leader: null, pendingHandoff: null };
    const html = renderWith("eng2", [approval()], { roster: none });
    expect(text(html)).toContain("Nobody can decide this yet: the run has no session leader.");
    expect(approvalLabel(approval(), null)).toBe("Waiting for a leader");
  });
  it("lists the pending count and the responsible person per request", () => {
    const html = renderWith("lead", [approval(), approval({ id: "a2" })]);
    expect(html).toContain("Pending decisions (2)");
    expect(html).toContain('data-testid="responsible-a1"');
    expect(html).toContain('data-testid="responsible-a2"');
    // An approved request awaiting apply is not a pending decision and is not counted as one.
    expect(renderWith("lead", [approval(), approval({ id: "a3", state: "approved", decidedBy: "lead" })])).toContain("Pending decisions (1)");
  });
});

describe("changed files show approval status", () => {
  it("shows the latest status per path, in words", () => {
    const list = [
      approval({ id: "a1", paths: ["a.py", "b.py"], state: "rejected", decidedBy: "lead" }),
      approval({ id: "a2", paths: ["b.py"], state: "pending" }),
      approval({ id: "a3", paths: ["c.py"], state: "approved", requester: "system:staging", decidedBy: "lead" }),
      approval({ id: "a4", paths: ["d.py"], state: "completed", decidedBy: "lead" }),
      approval({ id: "a5", paths: ["e.py"], state: "invalidated" }),
    ];
    expect(fileStatuses(list, "lead").map((f) => [f.path, f.label])).toEqual([
      ["a.py", "Rejected"], ["b.py", "Waiting for lead"], ["c.py", "Approved, not applied yet"],
      ["d.py", "Done"], ["e.py", "Voided — proposal or scope changed"],
    ]);
    const html = text(renderWith("lead", list));
    expect(html).toContain("b.py Waiting for lead");
    expect(html).toContain("c.py Approved, not applied yet");
  });
  it("says so when nothing needs a decision", () => {
    expect(renderWith("lead", [])).toContain("No changed files need a decision.");
  });
});

describe("readable decision history", () => {
  it("describes each outcome with who and what", () => {
    expect(describeDecision(approval({ state: "rejected", decidedBy: "lead" }))).toBe("lead rejected deleting src/old.py");
    expect(describeDecision(approval({ state: "completed", decidedBy: "lead", outcome: "applied 1 file(s)" })))
      .toBe("lead approved deleting src/old.py; applied (applied 1 file(s))");
    expect(describeDecision(approval({ state: "invalidated", outcome: "scope_change: new target" })))
      .toBe("Approval for deleting src/old.py was voided: scope_change: new target");
    expect(describeDecision(approval({ state: "expired" }))).toContain("expired before anyone decided");
  });
  it("renders decisions beside redirects and handoffs, in order, with a log role", () => {
    const events: CollabEvent[] = [
      { seq: 1, actor: "lead", kind: "leader.assigned", payload: {}, ts: "2026-10-18T10:00:00Z" },
      { seq: 2, actor: "eng2", kind: "redirect.guidance", payload: { content: "keep tests" }, ts: "2026-10-18T10:01:00Z" },
      { seq: 3, actor: "lead", kind: "handoff.requested", payload: { to: "eng2" }, ts: "2026-10-18T10:02:00Z" },
      { seq: 4, actor: "eng2", kind: "leader.takeover", payload: { from: "lead" }, ts: "2026-10-18T10:03:00Z" },
    ];
    const html = renderWith("lead", [approval({ state: "rejected", decidedBy: "lead" })], { events });
    const t = text(html);
    expect(t).toContain("lead became session leader");
    expect(t.indexOf("eng2 gave guidance: keep tests")).toBeGreaterThan(t.indexOf("lead became session leader"));
    expect(t).toContain("lead offered the lead to eng2");
    expect(t).toContain("eng2 took over the lead from lead");
    expect(t).toContain("lead rejected deleting src/old.py");
    expect(html).toContain('role="log" aria-label="Collaboration history"');
    expect(html).toContain('aria-label="Decision history"');
  });
  it("merges overlapping polls without duplicating or reordering", () => {
    const e = (seq: number): CollabEvent => ({ seq, actor: "a", kind: "participant.joined", payload: {}, ts: "" });
    const first = [e(1), e(2)];
    expect(mergeEvents(first, [e(2), e(3)]).map((x) => x.seq)).toEqual([1, 2, 3]);
    expect(mergeEvents(first, [e(1)])).toBe(first);
  });
});

describe("keyboard operability and status language", () => {
  const html = renderWith("lead", [approval(), approval({ id: "a2", state: "approved", requester: "system:staging", decidedBy: "lead" })]);
  it("uses native controls only: buttons have a type, no positive tabindex, scrollable diff is focusable", () => {
    for (const tag of html.match(/<button[^>]*>/g) ?? []) expect(tag).toMatch(/type="(button|submit)"/);
    expect(html).not.toMatch(/tabindex="[1-9]/i);
    expect(html).toMatch(/<pre aria-label="Proposed change" tabindex="0"/);
    expect(html).toContain("<form");
    expect(html).toMatch(/<button type="submit"/);
    expect(html).not.toMatch(/<(div|span|li)[^>]*role="button"/);
  });
  it("approve and reject point at the sentence naming who decides; cards can receive focus after acting", () => {
    const id = html.match(/aria-describedby="([^"]+)"/)![1];
    expect(html).toContain(`id="${id}"`);
    expect(html).toContain('data-approval-card="true" tabindex="-1"');
    expect(html).toContain('role="group" aria-label="Decide"');
  });
  it("announces state in words and through live regions, never colour alone", () => {
    expect(html).toContain('aria-label="Pending decisions" aria-live="polite"');
    expect(html).toContain('role="status" data-testid="blocked-banner"');
    for (const state of ["pending", "approved", "rejected", "invalidated", "expired", "executing", "completed", "failed"] as const) {
      expect(approvalLabel(approval({ state }), "lead").length).toBeGreaterThan(3);
    }
    expect(text(html)).toMatch(/Waiting for lead/);
    expect(renderWith("lead", [], { events: [] })).toContain('role="log"');
  });
});

describe("changed proposal re-requires approval", () => {
  it("detects approved -> pending with a new digest and shows the notice only then", () => {
    const before = [approval({ state: "approved", decidedBy: "lead" })];
    const after = [approval({ state: "pending", digest: "e".repeat(64) })];
    expect(reopenedApprovals(before, after)).toEqual(["a1"]);
    expect(reopenedApprovals(before, before)).toEqual([]);
    expect(reopenedApprovals([approval()], after)).toEqual([]);
    const html = renderWith("lead", after, { reopened: ["a1"] });
    expect(html).toContain('data-testid="reopened-a1"');
    expect(text(html)).toContain("The earlier approval no longer applies");
    expect(html).toContain("Approve this change");
    expect(html).toContain(">eeeeeeee<");
    expect(renderWith("lead", after)).not.toContain("reopened-a1");
  });
});

describe("no private reasoning in the shared view", () => {
  it("never prints event payload fields other than the vetted ones", () => {
    const events: CollabEvent[] = [
      { seq: 1, actor: "eng2", kind: "redirect.guidance", payload: { content: "use v2", reasoning: "SECRET-COT-1", thought: "SECRET-COT-2" }, ts: "" },
      { seq: 2, actor: "agent", kind: "agent.thinking", payload: { text: "SECRET-COT-3", chain_of_thought: "SECRET-COT-4" }, ts: "" },
      { seq: 3, actor: "lead", kind: "handoff.requested", payload: { to: "eng2", analysis: "SECRET-COT-5" }, ts: "" },
    ];
    const html = renderWith("lead", [], { events });
    expect(html).not.toMatch(/SECRET-COT/);
    expect(text(html)).toContain("agent: agent thinking");
    expect(describeEvent(events[1])).toBe("agent: agent thinking");
  });
  it("never prints undeclared approval fields (server-side reasoning stays out of the card)", () => {
    const leaky = { ...approval(), reasoning: "SECRET-COT-6", scratchpad: "SECRET-COT-7" } as Approval;
    expect(renderWith("lead", [leaky, { ...leaky, id: "a2", state: "completed", decidedBy: "lead" }])).not.toMatch(/SECRET-COT/);
  });
});
