// Multiplayer pilot S3 — model, client and static-render tests (node env, no jsdom).
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError } from "@/lib/api";
import { decideApproval, sendRedirect, type Approval, type Roster } from "./collabApi";
import { clipDiff, deriveCollabView, describeEvent, redirectProblem } from "./collabModel";
import { SharedRunView } from "./sharedRunPanel";

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
      busy={false} onJoin={noop} onClaim={noop} onDecide={noop} onHandoff={noop}
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
