// Part 47 UI: API client (mock fetch), view-model, bell, inbox, preferences, link opening.
// Node env: no DOM, so focus and real key events are not exercised (see docs/accessibility.md).
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError } from "@/lib/api";
import { button, disabled, expand, findAll, hasButton, press, textOf } from "../../tests/support/elementTree";
import {
  type NotificationItem, type Prefs, dismiss, getLink, getPrefs, getScopePrefs, getUnreadCount, listInbox, markRead, muteScope,
  notificationError, putPrefs, putScopePrefs, readAll, snooze, unmute, unsubscribe,
} from "./notificationsApi";
import {
  PREVIEWS, badgeText, bellLabel, deliveryText, filterQuery, formFromPrefs, hasRoute, isDirty, kindLabel, muteLabel, newSince,
  patchFromForm, previewText, quietProblem, safeInternalPath, scopeProblem,
} from "./notificationsModel";
import { BellView } from "./NotificationBell";
import { InboxView, type InboxActions } from "./inboxView";
import { resolveLink } from "./openNotification";
import { PrefsView, emptyDraft, type PrefsActions } from "../settings/notifications/prefsView";

afterEach(() => vi.unstubAllGlobals());

function stub(body: unknown = {}, status = 200) {
  const fn = vi.fn(async () => ({ ok: status < 400, status, text: async () => (body === undefined ? "" : JSON.stringify(body)) }));
  vi.stubGlobal("fetch", fn);
  return fn;
}
const call0 = (fn: ReturnType<typeof stub>) => {
  const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
  return { url, init, headers: (init.headers ?? {}) as Record<string, string>, body: init.body ? JSON.parse(init.body as string) : undefined };
};

const NOW = Date.parse("2026-10-19T12:00:00Z");
const item = (over: Partial<NotificationItem> = {}): NotificationItem => ({
  id: "n1", kind: "approval", state: "unread", urgent: false, count: 1, summaryCode: "approval_requested", sourceKind: "approval", sourceId: "a1",
  runId: "r1", sessionId: null, workspaceId: null, createdAt: "2026-10-19T11:00:00Z", updatedAt: "2026-10-19T11:30:00Z", snoozedUntil: null,
  delivery: { inapp: "delivered" }, link: "/api/v1/notifications/link/n1", ...over,
});

describe("API client", () => {
  it("unread count, paged list with filters and cursor", async () => {
    let fn = stub({ unread: 3, urgentUnread: 1 });
    expect(await getUnreadCount("tok")).toEqual({ unread: 3, urgentUnread: 1 });
    expect(call0(fn).url).toMatch(/\/api\/v1\/notifications\/unread-count$/);
    expect(call0(fn).headers.authorization).toBe("Bearer tok");
    fn = stub({ items: [], nextCursor: null });
    await listInbox("tok", { unread: true, kind: "mention", cursor: "abc=", limit: 10 });
    expect(call0(fn).url).toMatch(/\/api\/v1\/notifications\?limit=10&unread=true&kind=mention&cursor=abc%3D$/);
    fn = stub({ items: [] });
    await listInbox("tok", { state: "dismissed" });
    expect(call0(fn).url).toContain("limit=30&state=dismissed");
  });
  it("read, dismiss, snooze and read-all use the documented routes and bodies", async () => {
    let fn = stub(item()); await markRead("t", "n 1");
    expect(call0(fn).url).toContain("/notifications/n%201/read"); expect(call0(fn).init.method).toBe("POST");
    fn = stub(item()); await dismiss("t", "n1"); expect(call0(fn).url).toContain("/n1/dismiss");
    fn = stub(item()); await snooze("t", "n1", 180); expect(call0(fn).body).toEqual({ minutes: 180 });
    fn = stub({ updated: 2 }); await readAll("t", "mention"); expect(call0(fn).body).toEqual({ kind: "mention" });
    fn = stub({ updated: 2 }); await readAll("t"); expect(call0(fn).body).toEqual({});
  });
  it("link goes through /link/{id}", async () => {
    const fn = stub({ notificationId: "n1", path: "/projects/p/runs/r" });
    await getLink("t", "n1");
    expect(call0(fn).url).toMatch(/\/notifications\/link\/n1$/);
  });
  it("preferences: global and per scope, with the version that was read", async () => {
    let fn = stub({ version: 2 }); await getPrefs("t"); expect(call0(fn).url).toMatch(/preferences$/);
    fn = stub({ version: 3 }); await putPrefs("t", { email: "digest", expectedVersion: 2 });
    expect(call0(fn).init.method).toBe("PUT"); expect(call0(fn).body).toEqual({ email: "digest", expectedVersion: 2 });
    fn = stub({}); await getScopePrefs("t", "session", "s 1"); expect(call0(fn).url).toMatch(/preferences\/scopes\/session\/s%201$/);
    fn = stub({}); await putScopePrefs("t", "workspace", "w1", { inapp: "off" }); expect(call0(fn).url).toMatch(/scopes\/workspace\/w1$/);
  });
  it("mute and unmute; unmute is DELETE", async () => {
    let fn = stub({ id: "m1" }, 201); await muteScope("t", { scopeKind: "kind", scopeId: "mention", minutes: 60 });
    expect(call0(fn).body).toEqual({ scopeKind: "kind", scopeId: "mention", minutes: 60 });
    fn = stub(undefined, 204); await unmute("t", "m 1");
    expect(call0(fn).init.method).toBe("DELETE"); expect(call0(fn).url).toContain("/mute/m%201");
  });
  it("unsubscribe sends the emailed token and NO authorization header", async () => {
    let fn = stub({ unsubscribed: true, alreadyApplied: false });
    expect(await unsubscribe("tok-from-email")).toEqual({ unsubscribed: true, alreadyApplied: false });
    expect(call0(fn).body).toEqual({ token: "tok-from-email" });
    expect(call0(fn).headers.authorization).toBeUndefined();
    fn = stub({ detail: { error: { code: "NOT_FOUND", message: "x" } } }, 404);
    await expect(unsubscribe("expired")).rejects.toMatchObject({ status: 404, code: "NOT_FOUND" });
  });
  it.each([
    ["INVALID_TIMEZONE", 422, /time zone/i], ["INVALID_QUIET_HOURS", 422, /different times/i], ["ILLEGAL_STATE", 409, /dismissed/i],
    ["MUTE_CONFLICT", 409, /already muted/i], ["NOT_FOUND", 404, /no longer/i],
  ])("%s reads as plain language", (code, status, words) => {
    const m = notificationError(new ApiError("raw", code, status));
    expect(m).toMatch(words); expect(m).not.toContain(code);
  });
  it("a version conflict says the settings changed elsewhere", () => {
    expect(notificationError(new ApiError("raw", "VERSION_CONFLICT", 409))).toMatch(/changed somewhere else/);
  });
});

describe("preview text from the closed summary code", () => {
  const SERVER = ["approval_requested", "approval_decided", "mentioned", "handoff_offered", "handoff_accepted", "handoff_rejected", "run_failed", "review_failed", "run_blocked", "control_requested", "control_granted", "control_declined", "suggestion_queued", "budget_warning", "budget_exhausted"];
  it("covers exactly the codes the server defines (section1 notifications.py PREVIEWS)", () => {
    expect(Object.keys(PREVIEWS).sort()).toEqual([...SERVER].sort());
    // Parity with the server's own table, when the repo layout lets this test see it (it always does in the monorepo).
    const serverFile = new URL("../../../section1/src/harness_identity/notifications.py", import.meta.url);
    if (!existsSync(serverFile)) return;
    const src = readFileSync(serverFile, "utf8");
    for (const code of SERVER) expect(src).toContain(`"${code}": (`);
    for (const [code, [one, many]] of Object.entries(PREVIEWS)) {
      expect(src).toContain(`"${code}": ("${one}", "${many}")`); // same words as the server
    }
  });
  it("one item reads singular, a burst reads plural with the count", () => {
    expect(previewText("approval_requested", 1)).toBe("An approval needs your decision");
    expect(previewText("approval_requested", 3, "en")).toBe("3 approvals need your decision");
    expect(previewText("mentioned", 1200, "de")).toBe("You were mentioned 1.200 times");
  });
  it("an unknown code, an empty one and prototype keys read as a generic sentence", () => {
    for (const c of ["brand_new_code", "", "constructor", "__proto__", "toString"]) {
      expect(previewText(c, 1)).toBe("You have a notification");
      expect(previewText(c, 4, "en")).toBe("4 notifications");
    }
  });
  it("labels", () => {
    expect(kindLabel("control_request")).toBe("Control requests");
    expect(kindLabel("budget")).toBe("Spend warnings");
    expect(kindLabel("whatever")).toBe("Other");
  });
});

describe("bell", () => {
  it("the count is part of the accessible name; zero reads as none", () => {
    expect(bellLabel(0, 0)).toBe("Notifications, none unread");
    expect(bellLabel(3, 0, "en")).toBe("Notifications, 3 unread");
    expect(bellLabel(3, 1, "en")).toBe("Notifications, 3 unread, 1 urgent");
    expect(badgeText(7, "en")).toBe("7");
    expect(badgeText(250, "en")).toBe("99+");
  });
  it("renders a plain link to the inbox; the badge only counts", () => {
    const html = renderToStaticMarkup(<BellView unread={3} urgent={1} />);
    expect(html).toContain('href="/notifications"');
    expect(html).toContain('aria-label="Notifications, 3 unread, 1 urgent"');
    expect(html).toContain('data-testid="notification-badge"');
    expect(html).toContain('aria-hidden="true"');
    expect(renderToStaticMarkup(<BellView unread={0} urgent={0} />)).not.toContain("notification-badge");
  });
  it("announces growth politely and only on growth (first poll and drops are silent)", () => {
    expect(newSince(null, { unread: 4, urgentUnread: 0 })).toBeNull();
    expect(newSince({ unread: 4, urgentUnread: 0 }, { unread: 4, urgentUnread: 0 })).toBeNull();
    expect(newSince({ unread: 4, urgentUnread: 0 }, { unread: 2, urgentUnread: 0 })).toBeNull();
    expect(newSince({ unread: 1, urgentUnread: 0 }, { unread: 2, urgentUnread: 0 })).toBe("1 new notification");
    expect(newSince({ unread: 1, urgentUnread: 0 }, { unread: 4, urgentUnread: 1 })).toBe("3 new notifications, including an urgent one");
  });
  it("no sound, no desktop popup, no focus stealing anywhere in the notification UI", () => {
    const dirs = [new URL("./", import.meta.url), new URL("../settings/notifications/", import.meta.url)];
    const files = dirs.flatMap((d) => readdirSync(d).filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\./.test(f)).map((f) => new URL(f, d)));
    files.push(new URL("../../src/components/LiveAnnouncer.tsx", import.meta.url));
    expect(files.length).toBeGreaterThan(8);
    for (const f of files) {
      const src = readFileSync(f, "utf8");
      expect(src, String(f)).not.toMatch(/new Audio\(|<audio|AudioContext|new Notification\(|Notification\.requestPermission|navigator\.vibrate|window\.alert|\balert\(|autoFocus/);
    }
    expect(readFileSync(new URL("./NotificationBell.tsx", import.meta.url), "utf8")).not.toMatch(/\.focus\(/);
  });
});

describe("delivery state in words", () => {
  it("in-app is always delivered; email is mentioned only when not 'none'", () => {
    expect(deliveryText({ inapp: "delivered" })).toBe("Delivered in the app");
    expect(deliveryText({ inapp: "delivered", email: { state: "none", reason: null, attempts: 0, dueAt: null, lastAttemptAt: null } })).toBe("Delivered in the app");
    const e = (state: string, reason: string | null, attempts = 0) => deliveryText({ email: { state, reason, attempts, dueAt: null, lastAttemptAt: null } }, "en");
    expect(e("pending", "quiet_hours")).toBe("Delivered in the app. Email waiting: held until your quiet hours end");
    expect(e("pending", "retrying")).toMatch(/retrying/);
    expect(e("digest", null)).toMatch(/digest/);
    expect(e("sent", null)).toBe("Delivered in the app. Email sent");
    expect(e("failed", null, 3)).toBe("Delivered in the app. Email failed after 3 attempts");
    expect(e("skipped", "unsubscribed")).toMatch(/Email not sent: you unsubscribed/);
    expect(e("skipped", "handled_in_app")).toMatch(/already handled/);
    expect(e("mystery", null)).toMatch(/Email mystery/);
    expect(deliveryText(undefined)).toBe("Delivered in the app");
  });
});

describe("opening a notification", () => {
  it("accepts only same-site absolute paths", () => {
    expect(safeInternalPath("/projects/p1/runs/r1?focus=approval:a1")).toBe("/projects/p1/runs/r1?focus=approval:a1");
    for (const bad of ["//evil.example/x", "https://evil.example", "javascript:alert(1)", "/\\evil", "projects/p", "", null, 42, "/a\u0000b", "/a\nb"]) {
      expect(safeInternalPath(bad)).toBeNull();
    }
  });
  it("knows which paths exist in this app (run pages, the inbox); sessions and workspaces do not yet", () => {
    expect(hasRoute("/projects/p1/runs/r1")).toBe(true);
    expect(hasRoute("/projects/p1/runs/r1?focus=approval:a1")).toBe(true);
    expect(hasRoute("/notifications")).toBe(true);
    expect(hasRoute("/sessions/s1")).toBe(false);
    expect(hasRoute("/workspaces/w1")).toBe(false);
    expect(hasRoute("/projects/p1/runs/r1/../../x")).toBe(false);
  });
  it("go: the path the server returned; 404: a clear message and nothing to navigate to", async () => {
    stub({ notificationId: "n1", path: "/projects/p1/runs/r1?focus=approval:a1" });
    expect(await resolveLink("t", "n1")).toEqual({ kind: "go", path: "/projects/p1/runs/r1?focus=approval:a1" });
    stub({ detail: { error: { code: "NOT_FOUND", message: "not found" } } }, 404);
    const gone = await resolveLink("t", "n1");
    expect(gone).toMatchObject({ kind: "message", tone: "danger" });
    expect((gone as { text: string }).text).toMatch(/no longer open this/);
  });
  it("a server path that is not a same-site page, or a page this app lacks, is never navigated to", async () => {
    stub({ path: "https://evil.example/phish" });
    expect(await resolveLink("t", "n1")).toMatchObject({ kind: "message" });
    stub({ path: "/sessions/s1" });
    const r = await resolveLink("t", "n1");
    expect(r).toMatchObject({ kind: "message", tone: "info" });
    expect((r as { text: string }).text).toMatch(/does not have yet/);
  });
  it("other failures are shown in plain language", async () => {
    stub({}, 500);
    expect(await resolveLink("t", "n1")).toMatchObject({ kind: "message", tone: "danger" });
  });
});

function inboxActions(): InboxActions & Record<keyof InboxActions, ReturnType<typeof vi.fn>> {
  return { open: vi.fn(), markRead: vi.fn(), dismiss: vi.fn(), snooze: vi.fn(), toggleSnooze: vi.fn(), readAll: vi.fn(), more: vi.fn(), setFilter: vi.fn(), setKind: vi.fn() } as never;
}
const inbox = (over: Partial<Parameters<typeof InboxView>[0]> = {}) => {
  const a = inboxActions();
  const props = { items: [item()], nextCursor: null, filter: "active" as const, kind: "", busy: false, loading: false, message: null, snoozeFor: null, now: NOW, actions: a, ...over };
  return { a, html: () => renderToStaticMarkup(<InboxView {...props} />), tree: () => expand(InboxView(props)) };
};

describe("inbox", () => {
  it("shows the fixed sentence derived from the code, never the server's own text", () => {
    const hostile = { ...item({ summaryCode: "run_failed" }), preview: "<script>alert(1)</script> secret plan text" } as NotificationItem;
    const html = inbox({ items: [hostile] }).html();
    expect(html).toContain("A run failed");
    expect(html).not.toContain("secret plan text");
    expect(html).not.toContain("<script>");
  });
  it("an unknown summary code reads generically", () => {
    expect(inbox({ items: [item({ summaryCode: "from_the_future" })] }).html()).toContain("You have a notification");
  });
  it("urgency is the word Urgent; unread/read/snoozed states are words", () => {
    const html = inbox({ items: [item({ urgent: true }), item({ id: "n2", state: "read" }), item({ id: "n3", state: "snoozed", snoozedUntil: "2026-10-20T08:00:00Z" })] }).html();
    expect(html).toMatch(/>Urgent</);
    expect(html).toMatch(/Unread\./);
    expect(html).toMatch(/Read\./);
    expect(html).toMatch(/Snoozed until/);
    expect(html).toContain("Delivered in the app");
  });
  it("every action is a labelled button tied to the sentence it acts on; Open calls open", () => {
    const { tree, a } = inbox();
    const open = button(tree(), "Open");
    expect(open.props["aria-describedby"]).toBe("n-n1");
    press(open);
    expect(a.open).toHaveBeenCalledWith(expect.objectContaining({ id: "n1" }));
    const group = findAll(tree(), (n) => n.props.role === "group" && /Actions for: An approval needs your decision/.test(n.props["aria-label"] ?? ""));
    expect(group).toHaveLength(1);
  });
  it("mark read is offered for unread only, dismiss until dismissed, snooze not for dismissed", () => {
    expect(hasButton(inbox({ items: [item({ state: "read" })] }).tree(), "Mark read")).toBe(false);
    expect(hasButton(inbox({ items: [item({ state: "unread" })] }).tree(), "Mark read")).toBe(true);
    const dismissed = inbox({ items: [item({ state: "dismissed" })] }).tree();
    expect(hasButton(dismissed, "Dismiss")).toBe(false);
    expect(hasButton(dismissed, "Snooze")).toBe(false);
    press(button(inbox().tree(), "Mark read"));
  });
  it("snooze opens a menu of durations; choosing one snoozes by that many minutes; Escape closes", () => {
    const closed = inbox();
    press(button(closed.tree(), /^Snooze$/));
    expect(closed.a.toggleSnooze).toHaveBeenCalledWith("n1");
    expect(button(closed.tree(), /^Snooze$/).props["aria-expanded"]).toBe(false);
    const open = inbox({ snoozeFor: "n1" });
    expect(button(open.tree(), /^Snooze$/).props["aria-expanded"]).toBe(true);
    press(button(open.tree(), "Tomorrow"));
    expect(open.a.snooze).toHaveBeenCalledWith(expect.objectContaining({ id: "n1" }), 1440);
    const menu = findAll(open.tree(), (n) => n.props["aria-label"] === "Snooze for")[0];
    expect(textOf(menu)).toMatch(/1 hour.*3 hours.*Tomorrow.*Next week.*Cancel/);
    expect(press(menu, "onKeyDown", { key: "Escape" }).prevented).toBe(true);
    expect(open.a.toggleSnooze).toHaveBeenCalledWith(null);
  });
  it("mark all read follows the type filter and is disabled while busy", () => {
    expect(hasButton(inbox().tree(), "Mark all read")).toBe(true);
    const f = inbox({ kind: "mention" });
    expect(hasButton(f.tree(), "Mark all mentions read")).toBe(true);
    press(button(f.tree(), "Mark all mentions read"));
    expect(f.a.readAll).toHaveBeenCalled();
    expect(disabled(button(inbox({ busy: true }).tree(), "Mark all read"))).toBe(true);
  });
  it("paging by cursor, empty states and messages", () => {
    const more = inbox({ nextCursor: "cur" });
    press(button(more.tree(), "Load more"));
    expect(more.a.more).toHaveBeenCalled();
    expect(inbox({ nextCursor: null }).html()).not.toContain("Load more");
    expect(inbox({ items: [] }).html()).toContain("You are all caught up.");
    expect(inbox({ items: [], filter: "unread" }).html()).toContain("Nothing matches this filter.");
    const m = inbox({ message: { tone: "danger", text: "You can no longer open this" } }).html();
    expect(m).toMatch(/role="alert"[^>]*>You can no longer open this/);
    expect(inbox({ message: { tone: "info", text: "Opening" } }).html()).toContain('role="status"');
  });
  it("filters map to the API's query", () => {
    expect(filterQuery("active")).toEqual({});
    expect(filterQuery("unread")).toEqual({ unread: true });
    expect(filterQuery("dismissed")).toEqual({ state: "dismissed" });
    const html = inbox().html();
    expect(html).toContain("Unread only");
    expect(html).toContain("All types");
  });
  it("says plainly that nothing makes a sound or opens by itself", () => {
    expect(inbox().html()).toMatch(/Nothing here makes a sound, pops up or opens by itself/);
  });
});

const prefs = (over: Partial<Prefs> = {}): Prefs => ({
  scope: { kind: "global", id: null }, inapp: null, email: null, events: { mention: { email: "immediate" } }, version: 4,
  timezone: "Europe/London", quietHours: { enabled: true, start: "22:00", end: "07:00" }, digest: { period: "daily", hour: 8, lastSentAt: null },
  mutes: [{ id: "m1", scopeKind: "kind", scopeId: "mention", until: "2026-10-20T08:00:00Z", createdAt: "x", endedAt: null }],
  kinds: ["approval"], effective: { mention: { inapp: "on", email: "immediate" }, approval: { inapp: "on", email: "off" } }, ...over,
});

describe("preferences form <-> merge patch", () => {
  it("an untouched form sends nothing", () => {
    const f = formFromPrefs(prefs());
    expect(patchFromForm(f, f, "global", 4)).toEqual({});
    expect(isDirty(patchFromForm(f, f, "global", 4))).toBe(false);
  });
  it("sends only what changed, with the version that was read", () => {
    const o = formFromPrefs(prefs());
    const c = { ...o, email: "digest", events: { ...o.events, approval: { inapp: "off", email: "" } } };
    expect(patchFromForm(o, c, "global", 4)).toEqual({ email: "digest", events: { approval: { inapp: "off", email: null } }, expectedVersion: 4 });
  });
  it("clearing an event's last override removes the entry (null); inherit is null", () => {
    const o = formFromPrefs(prefs());
    const c = { ...o, events: { ...o.events, mention: { inapp: "", email: "" } } };
    expect(patchFromForm(o, c, "global", 4)).toEqual({ events: { mention: null }, expectedVersion: 4 });
    expect(patchFromForm({ ...o, inapp: "off" }, { ...o, inapp: "" }, "session", 4)).toMatchObject({ inapp: null });
  });
  it("quiet hours, time zone and digest are global-only", () => {
    const o = formFromPrefs(prefs());
    const c = { ...o, timezone: "Asia/Karachi", quietStart: "23:00", digestHour: 9 };
    expect(patchFromForm(o, c, "global", 4)).toEqual({ timezone: "Asia/Karachi", quietHours: { enabled: true, start: "23:00", end: "07:00" }, digest: { period: "daily", hour: 9 }, expectedVersion: 4 });
    expect(patchFromForm(o, c, "workspace", 4)).toEqual({});
  });
  it("no expectedVersion before the first save (version 0)", () => {
    const o = formFromPrefs(prefs({ version: 0 }));
    expect(patchFromForm(o, { ...o, email: "off" }, "global", 0)).toEqual({ email: "off" });
  });
  it("validates quiet hours and scope ids the way the API does", () => {
    const q = (s: string, e: string, on = true) => quietProblem({ quietEnabled: on, quietStart: s, quietEnd: e });
    expect(q("22:00", "07:00")).toBeNull();
    expect(q("22:00", "22:00")).toMatch(/different times/);
    expect(q("25:00", "07:00")).toMatch(/times/);
    expect(q("", "", false)).toBeNull();
    expect(scopeProblem("session", " ")).toMatch(/session id/);
    expect(scopeProblem("workspace", "w".repeat(81))).toMatch(/too long/);
    expect(scopeProblem("workspace", "w1")).toBeNull();
    expect(muteLabel({ scopeKind: "kind", scopeId: "mention" })).toBe("All mentions");
    expect(muteLabel({ scopeKind: "run", scopeId: "r1" })).toBe("Run r1");
  });
});

function prefsActions(): PrefsActions & Record<keyof PrefsActions, ReturnType<typeof vi.fn>> {
  return { setScope: vi.fn(), loadScope: vi.fn(), change: vi.fn(), changeEvent: vi.fn(), save: vi.fn(), mute: vi.fn(), unmute: vi.fn(), setDraft: vi.fn() } as never;
}
const pview = (over: Partial<Parameters<typeof PrefsView>[0]> = {}) => {
  const a = prefsActions();
  const p = prefs();
  const props = { scope: { kind: "global" as const, id: "" }, prefs: p, form: formFromPrefs(p), zones: ["UTC", "Europe/London"], draft: emptyDraft, busy: false, dirty: true, error: null, saved: null, actions: a, locale: "en", ...over };
  return { a, html: () => renderToStaticMarkup(<PrefsView {...props} />), tree: () => expand(PrefsView(props)) };
};

describe("preferences screen", () => {
  it("global: channels, per-event table with the result now, quiet hours with a time zone, digest", () => {
    const h = pview().html();
    expect(h).toContain("Quiet hours");
    expect(h).toContain('list="tz-list"');
    expect(h).toContain('<option value="Europe/London"');
    expect(h).toContain("Email digest");
    expect(h).toMatch(/<caption[^>]*>By type of event/);
    expect(h).toContain('aria-label="Mentions: email"');
    expect(h).toMatch(/In the app on, email right away/);
    expect(h).toContain("Never (default)");
    expect(h).toContain("never make a sound, open a pop-up or take focus");
    expect(h).toContain("Hold email during quiet hours");
  });
  it("workspace and session scopes show inherit options and no quiet hours, digest or mutes", () => {
    const h = pview({ scope: { kind: "workspace", id: "w1" } }).html();
    expect(h).toContain('<option value="">Inherit</option>');
    expect(h).not.toContain("Quiet hours");
    expect(h).not.toContain("Email digest");
    expect(h).not.toContain("Muted");
    expect(h).toContain("Workspace id");
  });
  it("the scope picker is a radio group; choosing a scope reports it", () => {
    const { tree, a, html } = pview();
    expect(html()).toContain('role="radiogroup" aria-label="Settings scope"');
    const radios = findAll(tree(), (n) => n.type === "input" && n.props.type === "radio");
    expect(radios).toHaveLength(3);
    press(radios[2], "onChange");
    expect(a.setScope).toHaveBeenCalledWith({ kind: "session", id: "" });
  });
  it("load needs a valid id", () => {
    const bad = pview({ scope: { kind: "session", id: "" }, form: null, prefs: null });
    expect(disabled(button(bad.tree(), "Load these settings"))).toBe(true);
    const ok = pview({ scope: { kind: "session", id: "s1" }, form: null, prefs: null });
    expect(disabled(button(ok.tree(), "Load these settings"))).toBe(false);
    press(button(ok.tree(), "Load these settings"));
    expect(ok.a.loadScope).toHaveBeenCalled();
  });
  it("save is disabled with no changes or invalid quiet hours; submit saves", () => {
    expect(pview({ dirty: false }).html()).toContain("No changes to save.");
    const clean = pview({ dirty: false });
    expect(disabled(button(clean.tree(), "Save settings"))).toBe(true);
    const f = formFromPrefs(prefs());
    const bad = pview({ form: { ...f, quietStart: "22:00", quietEnd: "22:00" } });
    expect(disabled(button(bad.tree(), "Save settings"))).toBe(true);
    expect(bad.html()).toMatch(/different times/);
    const ok = pview();
    const form = findAll(ok.tree(), (n) => n.type === "form")[0];
    expect(press(form, "onSubmit").prevented).toBe(true);
    expect(ok.a.save).toHaveBeenCalled();
  });
  it("mutes are listed with a labelled Unmute; a new mute carries kind, id and minutes", () => {
    const { tree, a, html } = pview();
    expect(html()).toContain("All mentions");
    press(button(tree(), "Unmute All mentions"));
    expect(a.unmute).toHaveBeenCalledWith(expect.objectContaining({ id: "m1" }));
    press(button(tree(), /^Mute$/));
    expect(a.mute).toHaveBeenCalledWith("kind", "approval", 60);
    const forever = pview({ draft: { kind: "run", id: " r9 ", duration: "" } });
    press(button(forever.tree(), /^Mute$/));
    expect(forever.a.mute).toHaveBeenCalledWith("run", "r9", undefined);
    expect(disabled(button(pview({ draft: { kind: "run", id: " ", duration: "" } }).tree(), /^Mute$/))).toBe(true);
  });
  it("errors alert, saves are polite status", () => {
    expect(pview({ error: "Nope" }).html()).toMatch(/role="alert"[^>]*>Nope/);
    expect(pview({ saved: "Settings saved." }).html()).toMatch(/role="status"[^>]*>Settings saved\./);
  });
  it("digest hours are formatted for the locale", () => {
    expect(pview({ locale: "en" }).html()).toMatch(/<option value="15"[^>]*>3\s?PM<\/option>/);
    expect(pview({ locale: "de" }).html()).toMatch(/<option value="15"[^>]*>15 Uhr<\/option>/);
  });
});
