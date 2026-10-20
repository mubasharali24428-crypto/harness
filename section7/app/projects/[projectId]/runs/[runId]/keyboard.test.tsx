// Part 56 (collaboration UI), keyboard-only operation at COMPONENT level. No browser exists in this
// environment, so this does NOT prove real Tab order, focus rings or key events. What it proves: every
// control is a native element (so Enter/Space/Tab work), has an accessible name, calls the right handler,
// moves focus to a surviving element before it disappears, and the key handlers (Ctrl+Enter, Escape) do
// what docs/accessibility.md says. The "react" module is mocked with the inert-state hooks of
// tests/support/elementTree.ts so hooked components can be driven without a DOM.
import { describe, expect, it, vi, beforeEach } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

vi.mock("react", async (orig) => {
  const actual = await orig<typeof import("react")>();
  const { fakeHooks } = await import("../../../../../tests/support/elementTree");
  return { ...actual, ...fakeHooks };
});

import { type Node, button, disabled, expand, findAll, hasButton, press, resetRuntime, textOf } from "../../../../../tests/support/elementTree";
import { type Approval, type CollabEvent, type Roster } from "./collabApi";
import { approvalAnnouncements, deriveCollabView, freshEventLines } from "./collabModel";
import { SharedRunView } from "./sharedRunPanel";
import { ControlStatus, SuggestionsWaiting } from "./controlSection";
import { HistoryLog, PAGE, loadEarlier, nearBottom, windowEvents } from "./historyLog";
import { ConnectionStatus } from "./connectionStatus";
import { Composer } from "./discussionView";

beforeEach(() => resetRuntime());

const roster: Roster = {
  participants: [{ userId: "lead", role: "leader" }, { userId: "eng2", role: "participant" }, { userId: "view", role: "observer" }],
  leader: "lead", pendingHandoff: null,
};
const approval = (over: Partial<Approval> = {}): Approval => ({
  id: "a1", runId: "r1", action: "delete", paths: ["src/old.py"], summary: "remove dead module", diff: "-import x",
  digest: "d".repeat(64), state: "pending", requester: "eng2", decidedBy: null, outcome: null, expiresAt: "2026-10-18T00:00:00Z", ...over,
});
function view(me: string, approvals: Approval[] = [approval()], r: Roster = roster) {
  const h = {
    onJoin: vi.fn(), onClaim: vi.fn(), onDecide: vi.fn(), onApply: vi.fn(), onHandoff: vi.fn(), onHandoffDecision: vi.fn(), onRedirect: vi.fn(),
  };
  const props = { me, roster: r, view: deriveCollabView(r, approvals, me, true), approvals, events: [] as CollabEvent[], error: null, busy: false, ...h };
  return { h, tree: () => expand(React.createElement(SharedRunView, props)), props, html: () => renderToStaticMarkup(React.createElement(SharedRunView, props)) };
}

/** A fake click event whose focus target records calls in order. */
function fakeClick(order: string[]) {
  const card = { focus: vi.fn(() => order.push("focus")) };
  const closest = vi.fn((sel: string) => ((sel === "[data-approval-card]" || sel === "[data-focus-park]") ? card : null));
  return { ev: { preventDefault: () => undefined, currentTarget: { closest } }, card, closest };
}

describe("approve / reject / apply are native buttons with names and keep focus", () => {
  it("the leader gets Approve and Reject as <button>, tied by aria-describedby to the sentence naming who decides", () => {
    const { tree } = view("lead");
    const approve = button(tree(), "Approve this change");
    const reject = button(tree(), "Reject");
    expect(approve.type).toBe("button");
    expect(reject.type).toBe("button");
    expect(approve.props.type).toBe("button");
    expect(approve.props["aria-describedby"]).toMatch(/-a1$/);
    expect(findAll(tree(), (n) => n.props.id === approve.props["aria-describedby"])).toHaveLength(1);
  });
  it("activating Approve moves focus to the approval card FIRST, then decides with the approval and true", () => {
    const { tree, h } = view("lead");
    const order: string[] = [];
    h.onDecide.mockImplementation(() => order.push("decide"));
    const c = fakeClick(order);
    button(tree(), "Approve this change").props.onClick(c.ev);
    expect(c.closest).toHaveBeenCalledWith("[data-approval-card]");
    expect(order).toEqual(["focus", "decide"]);
    expect(h.onDecide).toHaveBeenCalledWith(expect.objectContaining({ id: "a1" }), true);
  });
  it("Reject decides with false and also parks focus", () => {
    const { tree, h } = view("lead");
    const order: string[] = [];
    const c = fakeClick(order);
    button(tree(), "Reject").props.onClick(c.ev);
    expect(h.onDecide).toHaveBeenCalledWith(expect.objectContaining({ id: "a1" }), false);
    expect(c.card.focus).toHaveBeenCalledTimes(1);
  });
  it("the approval card can take programmatic focus (tabindex -1) and is a named group", () => {
    const card = findAll(view("lead").tree(), (n) => "data-approval-card" in n.props)[0];
    expect(card.props.tabIndex).toBe(-1);
    expect(card.props.role).toBe("group");
    expect(card.props["aria-label"]).toBe("Delete src/old.py");
  });
  it("a non-leader gets no decision buttons, only the reason in words", () => {
    const { tree } = view("eng2");
    expect(hasButton(tree(), /Approve|Reject/)).toBe(false);
    expect(textOf({ type: "x", props: {}, children: tree() })).toMatch(/Only lead can decide this/);
  });
  it("the diff is a labelled, focusable region so it can be scrolled with the keyboard", () => {
    const pre = findAll(view("lead").tree(), (n) => n.type === "pre")[0];
    expect(pre.props["aria-label"]).toBe("Proposed change");
    expect(pre.props.tabIndex).toBe(0);
  });
  it("Apply (a staged, approved change) is a button for the leader and parks focus", () => {
    const staged = approval({ id: "s1", requester: "system:staging", state: "approved", decidedBy: "lead" });
    const { tree, h } = view("lead", [staged]);
    const c = fakeClick([]);
    button(tree(), "Apply approved change").props.onClick(c.ev);
    expect(h.onApply).toHaveBeenCalledWith(expect.objectContaining({ id: "s1" }));
    expect(c.card.focus).toHaveBeenCalled();
  });
  it("buttons are disabled while a request is in flight (no double submit from a repeated Enter)", () => {
    const { props } = view("lead");
    const t = expand(React.createElement(SharedRunView, { ...props, busy: true }));
    expect(disabled(button(t, "Approve this change"))).toBe(true);
    expect(disabled(button(t, "Reject"))).toBe(true);
  });
});

describe("handoff", () => {
  it("the leader picks a teammate with a labelled <select> and offers; focus is parked", () => {
    const { tree, h } = view("lead", []);
    const select = findAll(tree(), (n) => n.type === "select" && n.props["aria-label"] === "Hand off lead to")[0];
    expect(select).toBeTruthy();
    expect(disabled(button(tree(), "Offer handoff"))).toBe(true); // nobody chosen yet
    press(select, "onChange", { target: { value: "eng2" } });
    // the select's state lives in the (mocked) hook runtime, so a re-expand sees the choice
    const t2 = tree();
    const offer = button(t2, "Offer handoff");
    expect(disabled(offer)).toBe(false);
    const c = fakeClick([]);
    offer.props.onClick(c.ev);
    expect(h.onHandoff).toHaveBeenCalledWith("eng2");
    expect(c.card.focus).toHaveBeenCalled();
  });
  it("the recipient accepts or declines with named buttons; focus is parked before the notice disappears", () => {
    const r = { ...roster, pendingHandoff: { id: "h1", from: "lead", to: "eng2" } };
    const { tree, h } = view("eng2", [], r);
    const a = fakeClick([]);
    button(tree(), "Accept").props.onClick(a.ev);
    expect(h.onHandoffDecision).toHaveBeenCalledWith("h1", true);
    expect(a.card.focus).toHaveBeenCalled();
    const d = fakeClick([]);
    button(tree(), "Decline").props.onClick(d.ev);
    expect(h.onHandoffDecision).toHaveBeenLastCalledWith("h1", false);
    const offer = findAll(tree(), (n) => n.props["data-testid"] === "handoff-offer")[0];
    expect(offer.props.role).toBe("status");
  });
});

describe("redirect (give direction)", () => {
  const typeInto = (tree: () => Node[], text: string) =>
    press(findAll(tree(), (n) => n.type === "textarea")[0], "onChange", { target: { value: text } });
  it("the form has labelled controls: type, direction, and a Send button", () => {
    const { tree } = view("lead", []);
    expect(findAll(tree(), (n) => n.type === "select" && n.props["aria-label"] === "Direction type")).toHaveLength(1);
    expect(findAll(tree(), (n) => n.type === "textarea" && n.props["aria-label"] === "Direction for the agent")).toHaveLength(1);
    expect(button(tree(), "Send direction").props.type).toBe("submit");
  });
  it("Ctrl+Enter in the box sends; plain Enter and Shift+Enter only add a line", () => {
    const { tree, h } = view("lead", []);
    typeInto(tree, "keep tests green");
    const box = () => findAll(tree(), (n) => n.type === "textarea")[0];
    expect(press(box(), "onKeyDown", { key: "Enter" }).prevented).toBe(false);
    expect(press(box(), "onKeyDown", { key: "Enter", shiftKey: true }).prevented).toBe(false);
    expect(h.onRedirect).not.toHaveBeenCalled();
    expect(press(box(), "onKeyDown", { key: "Enter", ctrlKey: true }).prevented).toBe(true);
    expect(h.onRedirect).toHaveBeenCalledWith("guidance", "keep tests green", "");
    // sent text is cleared
    expect(box().props.value).toBe("");
  });
  it("Cmd+Enter works too; an empty box never sends", () => {
    const { tree, h } = view("lead", []);
    const box = () => findAll(tree(), (n) => n.type === "textarea")[0];
    press(box(), "onKeyDown", { key: "Enter", metaKey: true });
    expect(h.onRedirect).not.toHaveBeenCalled();
    typeInto(tree, "go");
    press(box(), "onKeyDown", { key: "Enter", metaKey: true });
    expect(h.onRedirect).toHaveBeenCalledTimes(1);
  });
  it("busy blocks the shortcut too", () => {
    const { props } = view("lead", []);
    const tree = () => expand(React.createElement(SharedRunView, { ...props, busy: true }));
    typeInto(tree, "go");
    press(findAll(tree(), (n) => n.type === "textarea")[0], "onKeyDown", { key: "Enter", ctrlKey: true });
    expect(props.onRedirect).not.toHaveBeenCalled();
  });
  it("the shortcut is explained to the person", () => {
    expect(view("lead", []).html()).toMatch(/Ctrl\+Enter \(Cmd\+Enter on a Mac\)/);
  });
  it("an observer sees why there is no form instead of a dead form", () => {
    const { tree } = view("view", []);
    expect(findAll(tree(), (n) => n.type === "textarea")).toHaveLength(0);
  });
});

describe("suggestions: accept / reject", () => {
  const sugg = [{ id: "i1", author: "eng2", content: "Keep keep.txt", queuedAt: null, expiresAt: null }];
  it("the holder gets named native buttons tied to the suggestion text and focus is parked", () => {
    const onDecide = vi.fn();
    const t = expand(React.createElement(SuggestionsWaiting, { items: sugg, me: "lead", holder: "lead", isHolder: true, busy: false, onDecide }));
    const accept = button(t, "Accept");
    expect(accept.type).toBe("button");
    expect(accept.props["aria-describedby"]).toBeTruthy();
    const c = fakeClick([]);
    accept.props.onClick(c.ev);
    expect(onDecide).toHaveBeenCalledWith("i1", "accept");
    expect(c.card.focus).toHaveBeenCalled();
    press(button(t, "Reject"), "onClick", { currentTarget: { closest: () => ({ focus: () => undefined }) } });
    expect(onDecide).toHaveBeenLastCalledWith("i1", "reject");
  });
  it("control status buttons (take, request, release, hand over) are native buttons that park focus", () => {
    const onAcquire = vi.fn(), onRequest = vi.fn(), onRelease = vi.fn(), onQueue = vi.fn();
    const control = {
      holder: { userId: "lead", untimed: false, expiresAt: "x", expiresInSeconds: 600, connected: true, takeable: false },
      queue: [{ requestId: "q1", userId: "eng2", requestedAt: null, position: 1 }], queuedInstructions: [],
      viewer: { userId: "lead", isHolder: true, canAcquire: false, canRequest: false, canSuggest: true, queuePosition: null },
      how: { request: "POST /r", acquire: "POST /a", suggest: "x" },
    };
    const t = expand(React.createElement(ControlStatus, { control, busy: false, onAcquire, onRequest, onRelease, onQueue }));
    const c = fakeClick([]);
    button(t, "Release control").props.onClick(c.ev);
    expect(onRelease).toHaveBeenCalled();
    expect(c.card.focus).toHaveBeenCalled();
    button(t, "Hand over").props.onClick(fakeClick([]).ev);
    expect(onQueue).toHaveBeenCalledWith("q1", "grant");
    for (const b of findAll(t, (n) => n.type === "button")) expect(b.props.type ?? "button").toBe("button");
  });
});

describe("inline comment forms", () => {
  const policy = { maxBodyChars: 4000 };
  const make = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => {
    const onSubmit = vi.fn(), onCancel = vi.fn();
    const props = { label: "Reply to Ann", submitLabel: "Send reply", policy, people: ["ann", "bob"], me: "me", busy: false, formKey: "reply-c1", onSubmit, onCancel, ...extra };
    return { onSubmit, onCancel, tree: () => expand(React.createElement(Composer, props)) };
  };
  const box = (t: Node[]) => findAll(t, (n) => n.type === "textarea")[0];
  it("Ctrl+Enter sends the text with the mentioned participant ids; Escape cancels; Enter alone does neither", () => {
    const m = make();
    press(box(m.tree()), "onChange", { target: { value: "thanks @bob" } });
    expect(press(box(m.tree()), "onKeyDown", { key: "Enter" }).prevented).toBe(false);
    expect(m.onSubmit).not.toHaveBeenCalled();
    expect(press(box(m.tree()), "onKeyDown", { key: "Enter", ctrlKey: true }).prevented).toBe(true);
    expect(m.onSubmit).toHaveBeenCalledWith("thanks @bob", ["bob"]);
    expect(press(box(m.tree()), "onKeyDown", { key: "Escape" }).prevented).toBe(true);
    expect(m.onCancel).toHaveBeenCalledTimes(1);
  });
  it("a too-short or empty comment is not sent by the shortcut; busy blocks it", () => {
    const m = make();
    press(box(m.tree()), "onKeyDown", { key: "Enter", ctrlKey: true });
    expect(m.onSubmit).not.toHaveBeenCalled();
    const b = make({ busy: true });
    press(box(b.tree()), "onChange", { target: { value: "x" } });
    press(box(b.tree()), "onKeyDown", { key: "Enter", ctrlKey: true });
    expect(b.onSubmit).not.toHaveBeenCalled();
  });
  it("the field is named, described by the hint, and marks a problem for assistive tech", () => {
    const m = make();
    const f = box(m.tree());
    expect(f.props["aria-label"]).toBe("Reply to Ann");
    expect(f.props["aria-describedby"]).toMatch(/-hint$/);
    press(f, "onChange", { target: { value: "x".repeat(4001) } });
    expect(box(m.tree()).props["aria-invalid"]).toBe(true);
  });
  it("a new-comment form (no cancel) clears itself after sending", () => {
    const m = make({ onCancel: undefined, formKey: "new", label: "Add a comment" });
    press(box(m.tree()), "onChange", { target: { value: "hello" } });
    press(box(m.tree()), "onKeyDown", { key: "Enter", ctrlKey: true });
    expect(m.onSubmit).toHaveBeenCalledWith("hello", []);
    expect(box(m.tree()).props.value).toBe("");
  });
});

describe("long-history navigation", () => {
  const ev = (n: number): CollabEvent => ({ seq: n, actor: "u", kind: "participant.joined", payload: {}, ts: "2026-10-19T10:00:00Z" });
  const events = Array.from({ length: 250 }, (_, i) => ev(i + 1));
  it("renders only the newest window and says how many are hidden", () => {
    expect(windowEvents(events, PAGE).rows.map((e) => e.seq)[0]).toBe(151);
    expect(windowEvents(events, PAGE).hidden).toBe(150);
    expect(windowEvents(events, 10_000)).toMatchObject({ hidden: 0 });
    expect(windowEvents([], 100)).toEqual({ rows: [], hidden: 0 });
    const t = expand(React.createElement(HistoryLog, { events, locale: "en" }));
    expect(findAll(t, (n) => n.type === "li")).toHaveLength(PAGE);
    expect(button(t, /Load earlier/).type).toBe("button");
    expect(textOf({ type: "x", props: {}, children: t })).toContain("Load earlier (100 of 150 hidden)");
  });
  it("Load earlier widens the window by a page and stops at the total; the button goes away when nothing is hidden", () => {
    expect(loadEarlier(100, 250)).toBe(200);
    expect(loadEarlier(200, 250)).toBe(250);
    expect(loadEarlier(250, 250)).toBe(250);
    let t = expand(React.createElement(HistoryLog, { events, locale: "en" }));
    press(button(t, /Load earlier/));
    t = expand(React.createElement(HistoryLog, { events, locale: "en" }));
    expect(findAll(t, (n) => n.type === "li")).toHaveLength(200);
    press(button(t, /Load earlier/));
    t = expand(React.createElement(HistoryLog, { events, locale: "en" }));
    expect(findAll(t, (n) => n.type === "li")).toHaveLength(250);
    expect(hasButton(t, /Load earlier/)).toBe(false);
  });
  it("Jump to latest appears once the reader scrolls away from the end", () => {
    expect(nearBottom(0, 220, 220)).toBe(true);
    expect(nearBottom(0, 220, 2200)).toBe(false);
    expect(nearBottom(1970, 220, 2200)).toBe(true);
    const t = expand(React.createElement(HistoryLog, { events, locale: "en" }));
    expect(hasButton(t, "Jump to latest")).toBe(false);
    const log = findAll(t, (n) => n.props?.role === "log")[0];
    press(log, "onScroll", { currentTarget: { scrollTop: 0, clientHeight: 220, scrollHeight: 2200 } });
    const t2 = expand(React.createElement(HistoryLog, { events, locale: "en" }));
    expect(hasButton(t2, "Jump to latest")).toBe(true);
  });
  it("the log is a named, keyboard-focusable scroll region; the controls sit OUTSIDE the list", () => {
    const t = expand(React.createElement(HistoryLog, { events: events.slice(0, 5), locale: "en" }));
    const log = findAll(t, (n) => n.props?.role === "log")[0];
    expect(log.props.role).toBe("log");
    expect(log.props["aria-label"]).toBe("Collaboration history");
    expect(log.props.tabIndex).toBe(0);
    expect(findAll([log], (n) => n.type === "button")).toHaveLength(0);
    // role=log replaces a list's role, so the <li>s must sit in an <ol> INSIDE the log (axe: listitem).
    expect(findAll([log], (n) => n.type === "ol")).toHaveLength(1);
  });
  it("times are formatted for the viewer's locale, with the full date in the title", () => {
    const en = renderToStaticMarkup(React.createElement(HistoryLog, { events: [ev(1)], locale: "en-US" }));
    const de = renderToStaticMarkup(React.createElement(HistoryLog, { events: [ev(1)], locale: "de-DE" }));
    expect(en).toMatch(/<time[^>]*>\d{1,2}:\d{2}:\d{2}\s?[AP]M<\/time>/);
    expect(de).toMatch(/<time[^>]*>\d{2}:\d{2}:\d{2}<\/time>/);
    expect(en).toContain("dateTime=");
  });
});

describe("connection indicator", () => {
  it("is words, not a live region, and the stale/update state offers a keyboard-reachable reload", () => {
    const html = renderToStaticMarkup(React.createElement(ConnectionStatus, { status: "reconnecting", updateRequired: false }));
    expect(html).toContain("Reconnecting, refreshing every 3 s");
    expect(html).not.toContain("aria-live");
    expect(html).not.toContain("role=");
    const onReload = vi.fn();
    const t = expand(React.createElement(ConnectionStatus, { status: "live", updateRequired: true, onReload }));
    press(button(t, "Reload page"));
    expect(onReload).toHaveBeenCalled();
    expect(textOf({ type: "x", props: {}, children: t })).toMatch(/Live\. This page is out of date/);
  });
  it("the shared run panel shows it when given a connection", () => {
    const { props } = view("lead", []);
    const html = renderToStaticMarkup(React.createElement(SharedRunView, { ...props, connection: { status: "polling", updateRequired: false } }));
    expect(html).toContain('data-testid="connection-status"');
    expect(html).toContain("Live updates paused, refreshing every 3 s");
    expect(renderToStaticMarkup(React.createElement(SharedRunView, props))).not.toContain("connection-status");
  });
});

describe("what the shared run panel announces", () => {
  const a1 = approval();
  it("a new approval request is urgent for the person who must decide and polite for everyone else", () => {
    expect(approvalAnnouncements([], [a1], "lead", "lead")).toEqual([{ text: "Approval needed from you: delete src/old.py", urgent: true }]);
    expect(approvalAnnouncements([], [a1], "lead", "eng2")).toEqual([{ text: "New approval request: delete src/old.py", urgent: false }]);
    expect(approvalAnnouncements([], [a1], null, "eng2")[0].urgent).toBe(false); // no leader: nobody is "the one who must decide"
  });
  it("the first load, requests already known, and decided ones are silent", () => {
    expect(approvalAnnouncements(null, [a1], "lead", "lead")).toEqual([]);
    expect(approvalAnnouncements([a1], [a1], "lead", "lead")).toEqual([]);
    expect(approvalAnnouncements([], [approval({ state: "approved" })], "lead", "lead")).toEqual([]);
  });
  it("long path lists are clipped", () => {
    const long = approval({ id: "a9", paths: Array.from({ length: 30 }, (_, i) => `src/module_${i}.py`) });
    expect(approvalAnnouncements([], [long], "lead", "eng2")[0].text.length).toBeLessThan(120);
  });
  it("only fresh events by other people are spoken; replayed history and your own actions are silent", () => {
    const now = Date.parse("2026-10-19T12:00:00Z");
    const ev = (actor: string, ts: string, kind = "participant.joined") => ({ seq: 1, actor, kind, payload: {}, ts });
    expect(freshEventLines([ev("ann", "2026-10-19T11:59:30Z")], "me", now)).toEqual(["ann joined as participant"]);
    expect(freshEventLines([ev("me", "2026-10-19T11:59:30Z")], "me", now)).toEqual([]);
    expect(freshEventLines([ev("ann", "2026-10-19T10:00:00Z")], "me", now)).toEqual([]);
    expect(freshEventLines([{ ...ev("ann", "2026-10-19T11:59:59Z", "redirect.guidance"), payload: { content: "x".repeat(500) } }], "me", now)[0].length).toBeLessThanOrEqual(140);
  });
});
