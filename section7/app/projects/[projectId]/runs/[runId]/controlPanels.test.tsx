// Parts 41/42/45 UI — error messages, client calls and static-render tests (node env, no jsdom).
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError } from "@/lib/api";
import { sendRedirect } from "./collabApi";
import { deriveCollabView } from "./collabModel";
import { SharedRunView } from "./sharedRunPanel";
import {
  type AgentItem, type ConflictsView, type ControlView, type PresenceEntry, type PresenceView,
  agentAction, decideControl, friendlyError, howUrl, leavePresence, participation, resolveConflict,
} from "./controlApi";
import { ConflictList, ControlStatus, SuggestionsWaiting, expiryText } from "./controlSection";
import { PresenceRoster, changedAnnouncements, lastSeenText } from "./presenceSection";
import { AgentTree, buildAgentTree } from "./agentTree";

afterEach(() => vi.unstubAllGlobals());
const noop = () => undefined;

function stub(body: unknown = {}, status = 200) {
  const fn = vi.fn(async () => ({ ok: status < 400, status, text: async () => JSON.stringify(body) }));
  vi.stubGlobal("fetch", fn);
  return fn;
}
const lastCall = (fn: ReturnType<typeof stub>) => {
  const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
  return { url, init, body: init.body ? JSON.parse(init.body as string) : undefined };
};

describe("stable error codes read as plain language", () => {
  it.each([
    ["SESSION_FULL", /full/i], ["LEADER_MUST_HAND_OFF", /hand the lead/i], ["LEASE_HELD", /holds control/i],
    ["QUEUE_FULL", /too many suggestions/i], ["STALE_INTENT_VERSION", /changed/i], ["INTENT_CONFLICT", /conflict/i],
    ["PRESENCE_DISABLED", /turned presence off/i],
  ])("%s", (code, words) => {
    const msg = friendlyError(new ApiError("raw server text", code, 409));
    expect(msg).toMatch(words);
    expect(msg).not.toContain(code);
    expect(msg).not.toBe("raw server text");
  });
  it("unknown codes fall back to the server message", () => {
    expect(friendlyError(new ApiError("something odd", "WEIRD", 500))).toBe("something odd");
    expect(friendlyError(new Error("offline"))).toBe("offline");
  });
});

describe("client calls", () => {
  it("accept sends only the instruction id and decision; the sender's queued reply is typed", async () => {
    const fn = stub({ instructionId: "i1", state: "accepted", seq: 9 });
    await decideControl("r 1", "tok", { instructionId: "i1", decision: "accept" });
    const c = lastCall(fn);
    expect(c.url).toContain("/runs/r%201/control/decide");
    expect(c.body).toEqual({ instructionId: "i1", decision: "accept" });
    const q = stub({ queued: true, instructionId: "i2", seq: null, duplicate: false, invalidatedApprovals: 0 });
    const r = await sendRedirect("r1", "tok", { kind: "guidance", content: "x" });
    expect(r.queued).toBe(true);
    expect(q).toHaveBeenCalledOnce();
  });
  it("uses the routes the control view returns", async () => {
    expect(howUrl("POST /api/v1/orchestrator/runs/r1/control/request")).toMatch(/\/api\/v1\/orchestrator\/runs\/r1\/control\/request$/);
    expect(() => howUrl("nonsense")).toThrow();
  });
  it("resolve echoes the version seen; participation and leave hit the documented routes", async () => {
    let fn = stub();
    await resolveConflict("r1", "tok", "c 1", "keep_current", 4);
    expect(lastCall(fn).url).toContain("/intent/conflicts/c%201/resolve");
    expect(lastCall(fn).body).toEqual({ resolution: "keep_current", expectedVersion: 4 });
    fn = stub({ changed: true, announcement: "Ann is now observing" });
    expect((await participation("r1", "tok", "observe")).announcement).toBe("Ann is now observing");
    expect(lastCall(fn).url).toContain("/runs/r1/participation/observe");
    fn = stub();
    await leavePresence("r1", "tok", "tab-1");
    expect(lastCall(fn).url).toContain("/runs/r1/presence/leave");
    expect(lastCall(fn).init.keepalive).toBe(true);
    fn = stub();
    await agentAction("r1", "tok", "a1", "pause");
    expect(lastCall(fn).url).toContain("/runs/r1/agents/a1/pause");
  });
});

const sugg = [{ id: "i1", author: "p1-b", content: "Keep keep.txt unchanged", queuedAt: null, expiresAt: null }];
const suggestions = (isHolder: boolean, items = sugg) => renderToStaticMarkup(
  <SuggestionsWaiting items={items} me="p1-b" holder="p1-a" isHolder={isHolder} busy={false} onDecide={noop} />);

describe("suggestions waiting", () => {
  it("the holder gets labelled Accept and Reject buttons tied to the suggestion text", () => {
    const html = suggestions(true);
    expect(html).toContain('aria-label="Suggestions waiting"');
    expect(html).toContain("Keep keep.txt unchanged");
    expect(html).toContain(">Accept<");
    expect(html).toContain(">Reject<");
    expect(html).toMatch(/aria-describedby="[^"]+"/);
    expect(html).toContain("only if you accept");
  });
  it("the sender sees it is waiting for review, with no buttons", () => {
    const html = suggestions(false);
    expect(html).toContain("<strong>p1-b (you)</strong> suggests");
    expect(html).toContain("p1-a reviews these before the agent sees them");
    expect(html).toContain("Waiting for review.");
    expect(html).not.toContain(">Accept<");
  });
  it("renders nothing when empty and escapes hostile text", () => {
    expect(suggestions(true, [])).toBe("");
    const html = suggestions(true, [{ ...sugg[0], content: "<img src=x onerror=alert(1)>" }]);
    expect(html).not.toContain("<img src=x");
  });
});

const control = (over: Partial<ControlView["viewer"]> = {}, holder: ControlView["holder"] = {
  userId: "p1-a", untimed: false, expiresAt: "x", expiresInSeconds: 600, connected: true, takeable: false,
}): ControlView => ({
  holder, queue: [], queuedInstructions: [],
  viewer: { userId: "p1-b", isHolder: false, canAcquire: false, canRequest: true, canSuggest: true, queuePosition: null, ...over },
  how: { request: "POST /r", acquire: "POST /a", suggest: "x" },
});
const status = (c: ControlView) => renderToStaticMarkup(
  <ControlStatus control={c} busy={false} onAcquire={noop} onRequest={noop} onRelease={noop} onQueue={noop} />);

describe("control status", () => {
  it("shows holder and expiry; a non-holder can request, only the holder can release", () => {
    const html = status(control());
    expect(html).toContain("Held by <strong>p1-a</strong>");
    expect(html).toContain("Expires in about 10 min.");
    expect(html).toContain("Request control");
    expect(html).not.toContain("Release control");
    const mine = status(control({ isHolder: true, canRequest: false, userId: "p1-a" }));
    expect(mine).toContain("Release control");
    expect(mine).toContain("(you)");
  });
  it("free control offers Take control; queue shows who waits and lets the holder hand over", () => {
    expect(status(control({ canAcquire: true, canRequest: false }, null))).toContain("Nobody holds control.");
    expect(status(control({ canAcquire: true, canRequest: false }, null))).toContain("Take control");
    const c = control({ isHolder: true, canRequest: false, userId: "p1-a" });
    c.queue = [{ requestId: "q1", userId: "p1-b", requestedAt: null, position: 1 }];
    const html = status(c);
    expect(html).toContain("p1-b is waiting for control.");
    expect(html).toContain("Hand over");
  });
  it("words expiry without raw timestamps", () => {
    const h = control().holder!;
    expect(expiryText({ ...h, expiresInSeconds: 20 })).toBe("Expires in under a minute.");
    expect(expiryText({ ...h, untimed: true })).toBe("No expiry set.");
  });
});

const conflicts: ConflictsView = {
  currentApproved: { version: 3 }, openApprovals: [{}, {}],
  conflicts: [{
    id: "c1", kind: "conflict", author: "p1-b", state: "open", baseVersion: 1, stale: true,
    fields: [{ field: "goal", base: "old goal", current: "new goal", proposed: "their goal", conflicting: true },
      { field: "scopePaths", base: [], current: ["src"], proposed: ["src", "docs"], conflicting: false }],
  }],
};
describe("conflict view", () => {
  const html = (canResolve: boolean) => renderToStaticMarkup(<ConflictList data={conflicts} canResolve={canResolve} busy={false} onResolve={noop} />);
  it("shows base, current and proposed per field and warns about voided approvals", () => {
    const h = html(true);
    for (const t of ["old goal", "new goal", "their goal", "(clash)", "(none)", "src, docs"]) expect(h).toContain(t);
    expect(h).toContain("voids 2 open approvals");
    expect(h).toContain("Keep current");
    expect(h).toContain("Use their version");
  });
  it("only the holder or an admin gets resolve actions", () => {
    expect(html(false)).not.toContain("Keep current");
    expect(html(false)).toContain("Only the person in control or a team admin");
  });
  it("ignores resolved conflicts", () => {
    const done = { ...conflicts, conflicts: [{ ...conflicts.conflicts[0], state: "resolved" }] };
    expect(renderToStaticMarkup(<ConflictList data={done} canResolve busy={false} onResolve={noop} />)).toBe("");
  });
});

const entry = (over: Partial<PresenceEntry> = {}): PresenceEntry => ({
  userId: "p1-b", role: "participant", isLeader: false, self: false,
  presence: { status: "disconnected", lastSeen: "under_5_min" }, announcement: "Bea disconnected", ...over,
});
const roster = (participants: PresenceEntry[], mode: PresenceView["mode"] = "full") => ({
  mode, notice: "Presence is approximate.", participants,
});
const presence = (participants: PresenceEntry[], extra: Partial<React.ComponentProps<typeof PresenceRoster>> = {}, mode: PresenceView["mode"] = "full") =>
  renderToStaticMarkup(<PresenceRoster data={roster(participants, mode)} said="" error={null} busy={false} left={false} onAction={noop} {...extra} />);

describe("presence roster", () => {
  it("shows the server's status sentence and a bucketed last seen, no exact times", () => {
    const html = presence([entry()]);
    expect(html).toContain("Bea disconnected");
    expect(html).toContain("last seen under 5 minutes ago");
    expect(html).not.toMatch(/\d{4}-\d{2}-\d{2}/);
    expect(lastSeenText(entry({ presence: { status: "online" } }))).toBe("");
  });
  it("announces through a polite live region", () => {
    const html = presence([entry()], { said: "Ann is now observing" });
    expect(html).toMatch(/role="status" aria-live="polite" aria-atomic="true"[^>]*>Ann is now observing</);
  });
  it("participants may observe, leave or mute; observers may take part; leaders must hand off first", () => {
    const me = entry({ self: true, userId: "p1-a", preferences: { muted: false, notify: true, sharePresence: "team" } });
    const p = presence([me]);
    for (const t of ["Observe only", "Leave run", "Mute notifications"]) expect(p).toContain(t);
    expect(presence([{ ...me, role: "observer" }])).toContain("Take part");
    const lead = presence([{ ...me, role: "leader", isLeader: true }]);
    expect(lead).not.toContain("Leave run");
    expect(lead).toContain("Hand the lead to a teammate");
    expect(presence([{ ...me, preferences: { muted: true, notify: true, sharePresence: "team" } }])).toContain("Turn notifications on");
  });
  it("offers Rejoin only after leaving, and says when presence is off; errors are alerts", () => {
    expect(presence([entry()], { left: true })).toContain("Rejoin run");
    expect(presence([entry()])).not.toContain("Rejoin run");
    expect(presence([entry({ presence: null })], {}, "off")).toContain("turned presence off");
    expect(presence([entry()], { error: "This run is full." })).toContain('role="alert"');
  });
  it("derives announcements for other people's changes only", () => {
    const a = [entry(), entry({ userId: "me", self: true, announcement: "Me is online" })];
    expect(changedAnnouncements(null, a)).toEqual([]);
    const b = [entry({ announcement: "Bea is online" }), entry({ userId: "me", self: true, announcement: "Me is idle" })];
    expect(changedAnnouncements(a, b)).toEqual(["Bea is online"]);
    expect(changedAnnouncements(b, b)).toEqual([]);
  });
});

const agent = (over: Partial<AgentItem>): AgentItem => ({
  id: "a1", parentId: null, agentName: "coder", agentVersion: 2, title: "Fix tests", state: "active", pauseReason: null, depth: 0,
  budget: { allocatedUsd: "1", remainingUsd: "0.75", spentUsd: "0.1" }, progress: { percent: 40, note: null },
  recentToolCalls: [{ tool: "edit", summary: "edited a.py", outcome: "ok", createdAt: null }], ...over,
});
describe("agent subtask tree", () => {
  const items = [agent({}), agent({ id: "a2", parentId: "a1", title: "Write docs", state: "paused", pauseReason: "budget_exhausted", recentToolCalls: [] }),
    agent({ id: "a3", parentId: "gone", title: "Orphan" })];
  it("nests children and keeps orphans as roots", () => {
    const t = buildAgentTree(items);
    expect(t.map((n) => n.item.id)).toEqual(["a1", "a3"]);
    expect(t[0].children.map((n) => n.item.id)).toEqual(["a2"]);
  });
  const html = (canSteer: boolean) => renderToStaticMarkup(<AgentTree items={items} canSteer={canSteer} busy={false} error={null} said="" onAct={noop} />);
  it("shows state, budget left and the last tool summary", () => {
    const h = html(false);
    expect(h).toContain("$0.75 budget left");
    expect(h).toContain("40% done");
    expect(h).toContain("Last tool: edit, edited a.py");
    expect(h).toContain("paused: budget exhausted");
    expect(h).toContain("No tool calls yet");
  });
  it("steering buttons only for people who can steer; paused rows offer Resume", () => {
    expect(html(false)).not.toContain(">Pause<");
    const h = html(true);
    expect(h).toContain(">Pause<");
    expect(h).toContain(">Resume<");
    expect(h).toContain(">Cancel<");
    expect(h).toContain("do not stop a running process");
  });
  it("renders nothing without agents", () => {
    expect(renderToStaticMarkup(<AgentTree items={[]} canSteer busy={false} error={null} said="" onAct={noop} />)).toBe("");
  });
});

describe("shared panel mounts", () => {
  it("shows the queued-direction note as a polite status and renders the side sections", () => {
    const r = { participants: [{ userId: "p1-b", role: "participant" as const }], leader: "p1-a", pendingHandoff: null };
    const html = renderToStaticMarkup(
      <SharedRunView me="p1-b" roster={r} view={deriveCollabView(r, [], "p1-b", true)} approvals={[]} events={[]}
        error={null} busy={false} notice="Your direction is queued." onApply={noop} onJoin={noop} onClaim={noop} onDecide={noop}
        onHandoff={noop} onHandoffDecision={noop} onRedirect={noop}>
        <p data-testid="slot">side sections</p>
      </SharedRunView>);
    expect(html).toMatch(/role="status"[^>]*data-testid="direction-notice"[^>]*>Your direction is queued\./);
    expect(html).toContain("side sections");
  });
});
