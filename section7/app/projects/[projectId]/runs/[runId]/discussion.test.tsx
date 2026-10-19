// Part 43 UI: API client (mock fetch), view-model, and component behaviour (element-tree driver + static
// render). Node env: no DOM, so focus and real key events are not exercised (see docs/accessibility.md).
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError } from "@/lib/api";
import { describeEvent } from "./collabModel";
import { button, disabled, expand, findAll, hasButton, press, textOf } from "../../../../../tests/support/elementTree";
import {
  type CommentView, type DiscussionPolicy, commentError, createComment, deleteComment, editComment, exportComments, getCommentHistory,
  listComments, moderateComment, promoteComment, reactComment, searchDiscussions,
} from "./discussionApi";
import {
  bodyProblem, bodyView, buildThreads, canSeeExport, exportFileName, keyIntent, mergeComments, moderationLine, parseMentions, reactionChips, searchReady,
} from "./discussionModel";
import { type CommentActions, CommentCard, CommentsView, HistoryViewer, SearchResults, closedUi } from "./discussionView";

afterEach(() => vi.unstubAllGlobals());

function stub(body: unknown = {}, status = 200) {
  const fn = vi.fn(async () => ({ ok: status < 400, status, text: async () => JSON.stringify(body) }));
  vi.stubGlobal("fetch", fn);
  return fn;
}
const last = (fn: ReturnType<typeof stub>) => {
  const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
  return { url, init, body: init.body ? JSON.parse(init.body as string) : undefined };
};

const policy: DiscussionPolicy = {
  editWindowSeconds: 900, retentionDays: 365, auditSensitive: "auto", maxReplyDepth: 2, maxBodyChars: 4000,
  reactions: ["thumbs_up", "thumbs_down", "heart", "eyes", "celebrate", "question", "check"], refKinds: ["plan", "file"],
};
const NOW = Date.parse("2026-10-19T12:00:00Z");
const comment = (over: Partial<CommentView> = {}): CommentView => ({
  id: "c1", scopeKind: "run", scopeId: "r1", parentId: null, depth: 0,
  author: { userId: "ann", name: "Ann" }, body: "Looks good to me", state: "visible", purged: false,
  createdAt: "2026-10-19T11:50:00Z", editedAt: null, version: 1, edited: false, ref: null, reactions: {}, mentions: [],
  moderation: null, deleted: null, promotion: null, editableUntil: "2026-10-19T12:05:00Z",
  can: { edit: false, delete: false, react: true, reply: true, promote: false, moderate: false }, ...over,
});
const mine = (over: Partial<CommentView> = {}) => comment({ author: { userId: "me", name: "Me" }, can: { edit: true, delete: true, react: true, reply: true, promote: true, moderate: false }, ...over });

describe("API client", () => {
  const S = { kind: "run" as const, id: "r 1" };
  it("lists a run or a session with the documented paging", async () => {
    let fn = stub({ comments: [], nextCursor: null, policy });
    await listComments(S, "tok", { after: "c9", limit: 20 });
    expect(last(fn).url).toMatch(/\/api\/v1\/orchestrator\/runs\/r%201\/comments\?limit=20&after=c9$/);
    expect((last(fn).init.headers as Record<string, string>).authorization).toBe("Bearer tok");
    fn = stub({ comments: [], nextCursor: null, policy });
    await listComments({ kind: "session", id: "s1" }, "tok");
    expect(last(fn).url).toContain("/orchestrator/sessions/s1/comments?limit=50");
  });
  it("create carries parent, explicit mention ids and a request id; never an actor", async () => {
    const fn = stub({ id: "c2", duplicate: false, ignoredMentions: 0 }, 201);
    await createComment(S, "tok", { body: "hi @bob", parentId: "c1", mentions: ["bob"], requestId: "rq1" });
    expect(last(fn).init.method).toBe("POST");
    expect(last(fn).body).toEqual({ body: "hi @bob", parentId: "c1", mentions: ["bob"], requestId: "rq1" });
  });
  it("edit sends the version that was read; delete is DELETE; react is a set operation", async () => {
    let fn = stub({ changed: true });
    await editComment(S, "tok", "c1", { body: "x", expectedVersion: 3 });
    expect(last(fn).init.method).toBe("PATCH");
    expect(last(fn).url).toContain("/comments/c1");
    expect(last(fn).body).toEqual({ body: "x", expectedVersion: 3 });
    fn = stub({ id: "c1", state: "deleted", changed: true });
    await deleteComment(S, "tok", "c1");
    expect(last(fn).init.method).toBe("DELETE");
    fn = stub({ id: "c1", reactions: {} });
    await reactComment(S, "tok", "c1", "heart", false);
    expect(last(fn).url).toContain("/comments/c1/reactions");
    expect(last(fn).body).toEqual({ reaction: "heart", present: false });
  });
  it("promote names the explicit action and the version read", async () => {
    const fn = stub({ commentId: "c1", queued: true });
    await promoteComment(S, "tok", "c1", { expectedVersion: 2, runId: "r 1" });
    expect(last(fn).url).toContain("/comments/c1/promote");
    expect(last(fn).body).toEqual({ action: "send_as_guidance", expectedVersion: 2, runId: "r 1" });
  });
  it("history, export, moderate and search use the documented routes", async () => {
    let fn = stub({ revisions: [] });
    await getCommentHistory(S, "t", "c1");
    expect(last(fn).url).toMatch(/comments\/c1\/history$/);
    fn = stub({ comments: [] });
    await exportComments({ kind: "session", id: "s1" }, "t");
    expect(last(fn).url).toMatch(/sessions\/s1\/comments\/export$/);
    fn = stub({ id: "c1" });
    await moderateComment(S, "t", "c1", "hide", "spam link");
    expect(last(fn).body).toEqual({ action: "hide", reason: "spam link" });
    fn = stub({ results: [] });
    await searchDiscussions("t", "100% sure_", { scopeKind: "run", before: "c5", limit: 10 });
    expect(last(fn).url).toContain("/discussions/search?q=100%25+sure_&scopeKind=run&before=c5&limit=10");
  });
  it.each([
    ["EDIT_WINDOW_CLOSED", /time to edit/i], ["REPLY_TOO_DEEP", /nest/i], ["NOT_AUTHOR", /person who wrote/i], ["SCOPE_READ_ONLY", /read-only/i],
    ["COMMENT_UNAVAILABLE", /hidden, deleted or expired/i], ["REASON_REQUIRED", /three characters/i], ["BAD_QUERY", /two characters/i],
  ])("%s reads as plain language, not a code", (code, words) => {
    const msg = commentError(new ApiError("raw", code, 409));
    expect(msg).toMatch(words);
    expect(msg).not.toContain(code);
  });
  it("a stale comment version says the view changed; unknown codes fall back to the server text", () => {
    expect(commentError(new ApiError("raw", "STALE_COMMENT_VERSION", 409))).toMatch(/changed while you were looking/);
    expect(commentError(new ApiError("odd", "WEIRD", 500))).toBe("odd");
    expect(commentError(new Error("offline"))).toBe("offline");
  });
});

describe("comment events in the team history say what happened and nothing else", () => {
  it.each([
    ["comment.created", "ann commented"], ["comment.edited", "ann edited a comment"], ["comment.deleted", "ann deleted a comment"],
    ["comment.hidden", "ann hid a comment"], ["comment.restored", "ann restored a comment"], ["comment.promoted", "ann sent a comment to the agent as guidance"],
  ])("%s", (kind, line) => {
    expect(describeEvent({ seq: 1, actor: "ann", kind, payload: { content: "SECRET TEXT" }, ts: "" })).toBe(line);
  });
});

describe("view-model", () => {
  it("builds threads from the flat list; a reply whose parent is not loaded becomes a root", () => {
    const t = buildThreads([
      comment({ id: "a" }), comment({ id: "b", parentId: "a", depth: 1 }), comment({ id: "c", parentId: "b", depth: 2 }),
      comment({ id: "orphan", parentId: "not-loaded", depth: 1 }),
    ]);
    expect(t.map((n) => n.c.id)).toEqual(["a", "orphan"]);
    expect(t[0].children[0].children[0].c.id).toBe("c");
  });
  it("merging a fresh page replaces by id, appends new ones and keeps chronological order", () => {
    const m = mergeComments(
      [comment({ id: "a", createdAt: "2026-01-01T00:00:00Z", body: "old" }), comment({ id: "c", createdAt: "2026-01-03T00:00:00Z" })],
      [comment({ id: "a", createdAt: "2026-01-01T00:00:00Z", body: "new" }), comment({ id: "b", createdAt: "2026-01-02T00:00:00Z" })],
    );
    expect(m.map((c) => c.id)).toEqual(["a", "b", "c"]);
    expect(m[0].body).toBe("new");
  });
  it("shows text, tombstones, hidden and expired comments honestly", () => {
    expect(bodyView(comment())).toEqual({ kind: "text", text: "Looks good to me", note: null });
    expect(bodyView(comment({ state: "deleted", body: null, deleted: { at: "x", by: "ann" } }))).toEqual({ kind: "placeholder", text: "This comment was deleted." });
    expect(bodyView(comment({ state: "deleted", body: null, deleted: { at: "x", by: "mod1" } })).text).toMatch(/deleted by mod1/);
    expect(bodyView(comment({ state: "hidden", body: null }))).toEqual({ kind: "placeholder", text: "A moderator hid this comment." });
    expect(bodyView(comment({ state: "hidden", body: "secret" }))).toMatchObject({ kind: "text", text: "secret", note: expect.stringMatching(/Hidden from everyone/) });
    expect(bodyView(comment({ purged: true, body: null })).text).toMatch(/retention period/);
  });
  it("moderation state reads as a sentence with who and why", () => {
    expect(moderationLine(comment({ moderation: { state: "hidden", by: "admin1", at: "x", reason: "off topic" } }))).toBe("Hidden by admin1: off topic");
    expect(moderationLine(comment())).toBeNull();
  });
  it("mentions are explicit ids of known participants: not yourself, not strangers, not mid-word, no duplicates", () => {
    const people = ["ann", "bob", "p1-b", "ann2"];
    expect(parseMentions("hi @ann and @p1-b!", people, "me")).toEqual(["p1-b", "ann"].sort((a, b) => b.length - a.length));
    expect(parseMentions("@me @stranger mail@ann", people, "me")).toEqual([]);
    expect(parseMentions("@ann @ann", people, "me")).toEqual(["ann"]);
    expect(parseMentions("@ann2 only", people, "me")).toEqual(["ann2"]);
    expect(parseMentions("@ann", ["ann"], "ann")).toEqual([]);
    expect(parseMentions("x".repeat(5), people, "me")).toEqual([]);
  });
  it("at most 10 mentions are sent", () => {
    const ids = Array.from({ length: 14 }, (_, i) => `u${i}`);
    expect(parseMentions(ids.map((i) => `@${i}`).join(" "), ids, "me")).toHaveLength(10);
  });
  it("reaction chips come from the API's closed set, with counts and 'mine'", () => {
    const chips = reactionChips(comment({ reactions: { heart: { count: 2, mine: true } } }), { reactions: ["thumbs_up", "heart"] });
    expect(chips).toEqual([
      { code: "thumbs_up", name: "Thumbs up", count: 0, mine: false }, { code: "heart", name: "Heart", count: 2, mine: true },
    ]);
  });
  it("body problems, key intents, export gating and file name, search readiness", () => {
    expect(bodyProblem("  ", policy)).toMatch(/Write something/);
    expect(bodyProblem("x".repeat(4001), policy)).toMatch(/limited to 4000/);
    expect(bodyProblem("ok", policy)).toBeNull();
    expect(keyIntent({ key: "Escape" })).toBe("cancel");
    expect(keyIntent({ key: "Enter", ctrlKey: true })).toBe("submit");
    expect(keyIntent({ key: "Enter", metaKey: true })).toBe("submit");
    expect(keyIntent({ key: "Enter" })).toBeNull();
    expect(keyIntent({ key: "a", ctrlKey: true })).toBeNull();
    expect(["admin", "engineer"].map(canSeeExport)).toEqual([true, true]);
    expect(["viewer", "guest", undefined].map(canSeeExport)).toEqual([false, false, false]);
    expect(exportFileName("run", "r/1 ../x", new Date("2026-10-19T00:00:00Z"))).toBe("discussion-run-r_1_x-2026-10-19.json");
    expect(searchReady("a")).toBe(false);
    expect(searchReady(" ab ")).toBe(true);
  });
});

function actions(): CommentActions & Record<keyof CommentActions, ReturnType<typeof vi.fn>> {
  return { open: vi.fn(), close: vi.fn(), submitEdit: vi.fn(), submitReply: vi.fn(), react: vi.fn(), remove: vi.fn(), promote: vi.fn(), moderate: vi.fn() } as never;
}
const card = (c: CommentView, over: Partial<Parameters<typeof CommentCard>[0]> = {}) => {
  const a = actions();
  const props = { c, me: "me", canSteer: false, canComment: true, policy, people: ["ann", "bob"], busy: false, ui: closedUi, now: NOW, actions: a, ...over };
  return { a, props, tree: () => expand(CommentCard(props)), html: () => renderToStaticMarkup(<CommentCard {...props} />) };
};

describe("comment card", () => {
  it("shows author, relative time, edited marker and the text; the article is named", () => {
    const { html } = card(comment({ edited: true, version: 2, editedAt: "2026-10-19T11:55:00Z" }));
    expect(html()).toContain('aria-label="Comment by Ann"');
    expect(html()).toContain("Looks good to me");
    expect(html()).toMatch(/10 minutes ago/);
    expect(html()).toMatch(/edited <time[^>]*>5 minutes ago/);
  });
  it("renders hostile text as text, never as markup", () => {
    const html = card(comment({ body: '<img src=x onerror="alert(1)"><script>alert(2)</script>' })).html();
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<script");
    expect(html).toContain("&lt;img");
    for (const f of ["discussionView.tsx", "discussionSection.tsx", "discussionModel.ts", "discussionApi.ts"]) {
      expect(readFileSync(new URL(f, import.meta.url), "utf8")).not.toMatch(/dangerouslySetInnerHTML|innerHTML/);
    }
  });
  it("another person's comment: you can reply and react, not edit, delete or promote", () => {
    const { tree } = card(comment(), { canSteer: true });
    expect(hasButton(tree(), "Reply")).toBe(true);
    expect(hasButton(tree(), /^Edit$/)).toBe(false);
    expect(hasButton(tree(), /^Delete$/)).toBe(false);
    expect(hasButton(tree(), "Send as guidance")).toBe(false); // can.promote is false from the server
  });
  it("your own comment inside the edit window: Edit and Delete, with the deadline stated; clicking only opens", () => {
    const { tree, a, html } = card(mine());
    expect(html()).toMatch(/You can edit this until <time/);
    press(button(tree(), /^Edit$/));
    expect(a.open).toHaveBeenCalledWith("c1", "edit");
    press(button(tree(), /^Delete$/));
    expect(a.open).toHaveBeenCalledWith("c1", "delete");
    expect(a.remove).not.toHaveBeenCalled();
  });
  it("after the window closes the server stops offering edit (can.edit false) and the UI follows it", () => {
    const { tree } = card(mine({ can: { edit: false, delete: true, react: true, reply: true, promote: true, moderate: false }, editableUntil: null }));
    expect(hasButton(tree(), /^Edit$/)).toBe(false);
    expect(hasButton(tree(), /^Delete$/)).toBe(true);
  });
  it("edit opens an inline form named for the task; reply likewise", () => {
    const e = card(mine(), { ui: { open: { id: "c1", kind: "edit" } } }).html();
    expect(e).toContain('data-testid="composer-edit-c1"');
    expect(e).toContain('aria-label="Edit your comment"');
    expect(e).toContain("Looks good to me</textarea>");
    expect(e).toContain("Ctrl+Enter sends, Escape cancels");
    const r = card(comment(), { ui: { open: { id: "c1", kind: "reply" } } }).html();
    expect(r).toContain('aria-label="Reply to Ann"');
  });
  it("delete asks first, says what remains, and only the confirm button deletes", () => {
    const { tree, a } = card(mine(), { ui: { open: { id: "c1", kind: "delete" } } });
    const t = tree();
    const group = findAll(t, (n) => n.props.role === "group" && n.props["aria-label"] === "Confirm delete")[0];
    expect(textOf(group)).toMatch(/place in the thread stays/);
    press(button([group], "Delete comment"));
    expect(a.remove).toHaveBeenCalledTimes(1);
    press(button([group], "Cancel"));
    expect(a.close).toHaveBeenCalledTimes(1);
  });
  it("promote is hidden unless you can steer, then needs an explicit confirmation naming the version and the consequences", () => {
    expect(hasButton(card(mine(), { canSteer: false }).tree(), "Send as guidance")).toBe(false);
    const closed = card(mine(), { canSteer: true });
    press(button(closed.tree(), "Send as guidance"));
    expect(closed.a.open).toHaveBeenCalledWith("c1", "promote");
    expect(closed.a.promote).not.toHaveBeenCalled(); // opening is not sending
    const open = card(mine({ version: 3 }), { canSteer: true, ui: { open: { id: "c1", kind: "promote" } } });
    const group = findAll(open.tree(), (n) => n.props["aria-label"] === "Confirm send as guidance")[0];
    expect(textOf(group)).toMatch(/version 3/);
    expect(textOf(group)).toMatch(/cannot be taken back/);
    expect(textOf(group)).toMatch(/someone else holds control, it waits/);
    press(button([group], "Send as guidance"));
    expect(open.a.promote).toHaveBeenCalledTimes(1);
  });
  it("Escape closes a confirmation; other keys do not", () => {
    const { tree, a } = card(mine(), { canSteer: true, ui: { open: { id: "c1", kind: "promote" } } });
    const group = findAll(tree(), (n) => n.props["aria-label"] === "Confirm send as guidance")[0];
    expect(press(group, "onKeyDown", { key: "x" }).prevented).toBe(false);
    expect(a.close).not.toHaveBeenCalled();
    expect(press(group, "onKeyDown", { key: "Escape" }).prevented).toBe(true);
    expect(a.close).toHaveBeenCalledTimes(1);
  });
  it("the confirmation group can take programmatic focus and is findable by the container", () => {
    const html = card(mine(), { canSteer: true, ui: { open: { id: "c1", kind: "promote" } } }).html();
    expect(html).toMatch(/role="group" aria-label="Confirm send as guidance" tabindex="-1" data-open-form="promote-c1"/);
  });
  it("reactions are toggle buttons with a state and a count in the accessible name", () => {
    const { tree, a } = card(comment({ reactions: { heart: { count: 2, mine: true }, eyes: { count: 1, mine: false } } }));
    const heart = button(tree(), /Heart, 2 people, including you/);
    expect(heart.props["aria-pressed"]).toBe(true);
    press(heart);
    expect(a.react).toHaveBeenCalledWith(expect.objectContaining({ id: "c1" }), "heart", false); // mine -> remove
    press(button(tree(), /Eyes, 1 person$/));
    expect(a.react).toHaveBeenLastCalledWith(expect.anything(), "eyes", true);
    expect(findAll(tree(), (n) => n.type === "button" && /Thumbs up/.test(n.props["aria-label"] ?? ""))).toHaveLength(1); // closed set, all offered
  });
  it("controls are disabled while busy", () => {
    const { tree } = card(mine(), { busy: true });
    expect(disabled(button(tree(), /^Edit$/))).toBe(true);
    expect(disabled(button(tree(), /^Delete$/))).toBe(true);
  });
  it("a deleted comment keeps its place as a tombstone: no text, no reactions, no edit", () => {
    const c = card(mine({ state: "deleted", body: null, deleted: { at: "x", by: "me" }, can: { edit: false, delete: false, react: false, reply: false, promote: false, moderate: false } }));
    expect(c.html()).toContain("This comment was deleted.");
    expect(c.html()).toContain("Deleted");
    expect(c.html()).not.toContain("Reactions to");
    expect(hasButton(c.tree(), /Edit|Delete|Reply|guidance/)).toBe(false);
  });
  it("a hidden comment shows its moderation line; someone who cannot read it sees a placeholder", () => {
    const html = card(comment({ state: "hidden", body: null, moderation: { state: "hidden", by: "admin1", at: "x", reason: "off topic" } })).html();
    expect(html).toContain("A moderator hid this comment.");
    expect(html).toContain("Hidden by admin1: off topic");
  });
  it("a promoted comment says who sent which version, and when it is still queued", () => {
    const html = card(comment({ promotion: { runId: "r1", by: "lead", at: "x", commentVersion: 2, seq: null, instructionId: "i1" }, can: { edit: false, delete: false, react: true, reply: true, promote: false, moderate: false } })).html();
    expect(html).toContain("lead sent version 2 to the agent as guidance (waiting for the person in control to accept)");
    expect(html).toContain("Sent as guidance");
  });
  it("an unavailable reference leaks nothing and says so", () => {
    expect(card(comment({ ref: { kind: "approval", available: false } })).html()).toContain("The approval this refers to is no longer available.");
    expect(card(comment({ ref: { kind: "file", id: "src/a.py", pos: 12, available: true } })).html()).toContain("About: file src/a.py, line 12");
  });
  it("history opens as a named region, loading then ready, with the original labelled", () => {
    const loading = renderToStaticMarkup(<HistoryViewer state={{ status: "loading" }} onClose={() => undefined} />);
    expect(loading).toContain('aria-label="Edit history"');
    expect(loading).toContain("Loading history");
    const ready = renderToStaticMarkup(
      <HistoryViewer now={NOW} onClose={() => undefined} state={{ status: "ready", data: { commentId: "c1", state: "visible", purged: false, author: { userId: "ann", name: "Ann" }, createdAt: "x", currentVersion: 2, revisions: [
        { version: 1, editedBy: "ann", editedAt: "2026-10-19T11:00:00Z", body: "first" }, { version: 2, editedBy: "ann", editedAt: "2026-10-19T11:30:00Z", body: "second <b>" }, { version: 3, editedBy: "ann", editedAt: null, body: null },
      ] } }} />);
    expect(ready).toMatch(/Original by ann/);
    expect(ready).toMatch(/Version 2 by ann/);
    expect(ready).toContain("second &lt;b&gt;");
    expect(ready).toContain("You cannot see this text.");
    const err = renderToStaticMarkup(<HistoryViewer state={{ status: "error", message: "nope" }} onClose={() => undefined} />);
    expect(err).toContain('role="alert"');
  });
  it("the history button appears only for edited comments and reports its expanded state", () => {
    expect(hasButton(card(comment()).tree(), "History")).toBe(false);
    const t = card(comment({ edited: true, version: 2 }), { ui: { open: { id: "c1", kind: "history" } } }).tree();
    expect(button(t, "History").props["aria-expanded"]).toBe(true);
  });
});

describe("comments panel", () => {
  const base = (over: Partial<Parameters<typeof CommentsView>[0]> = {}) => ({
    comments: [comment({ id: "a" }), comment({ id: "b", parentId: "a", depth: 1, createdAt: "2026-10-19T11:51:00Z" })],
    policy, me: "me", canSteer: true, canComment: true, people: ["ann"], busy: false, error: null, notice: null, ui: closedUi, histories: {},
    hasMore: false, loading: false, scopeLabel: "run", canExport: false, now: NOW,
    search: { q: "", busy: false, page: null, scope: { kind: "run", id: "r1" } },
    actions: actions(), onCreate: vi.fn(), onLoadMore: vi.fn(), onSearchChange: vi.fn(), onSearch: vi.fn(), onSearchMore: vi.fn(), onShow: vi.fn(), onExport: vi.fn(),
    ...over,
  });
  const html = (o = {}) => renderToStaticMarkup(<CommentsView {...base(o)} />);
  it("says comments are never sent to the agent by themselves", () => {
    expect(html()).toContain("never sent to the agent unless someone with permission chooses");
  });
  it("threads nest replies inside their parent's list item", () => {
    const h = html();
    expect(h).toMatch(/aria-label="Comments"/);
    expect(h).toMatch(/<ul aria-label="Replies"/);
    expect(h.indexOf('data-testid="comment-b"')).toBeGreaterThan(h.indexOf('data-testid="comment-a"'));
    expect(h).toContain('aria-label="Reply by Ann"');
  });
  it("export is offered only when permitted; viewers and guests get a read-only note", () => {
    expect(html({ canExport: true })).toContain("Export discussion (JSON)");
    expect(html({ canExport: false })).not.toContain("Export discussion");
    const ro = html({ canComment: false });
    expect(ro).toContain("can read the discussion but not write in it");
    expect(ro).not.toContain('data-testid="composer-new"');
  });
  it("a search form is a labelled search landmark and needs two characters", () => {
    const h = html({ search: { q: "a", busy: false, page: null, scope: { kind: "run", id: "r1" } } });
    expect(h).toContain('role="search"');
    expect(h).toContain('aria-label="Search comments"');
    expect(h).toMatch(/<button type="submit"[^>]*disabled/);
    expect(html({ search: { q: "ab", busy: false, page: null, scope: { kind: "run", id: "r1" } } })).not.toMatch(/<button type="submit"[^>]*disabled=""[^>]*>Search/);
  });
  it("load more, errors and notices", () => {
    expect(html({ hasMore: true })).toContain("Load more comments");
    expect(html({ error: "Boom" })).toContain('role="alert"');
    expect(html({ notice: "Sent" })).toContain('role="status"');
  });
  it("empty state", () => {
    expect(html({ comments: [] })).toContain("No comments yet.");
  });
  it("search results offer 'Show in thread' only for loaded comments of this scope, as plain text", () => {
    const hits = [
      { commentId: "a", scopeKind: "run" as const, scopeId: "r1", parentId: null, author: { userId: "ann", name: "Ann" }, snippet: "found <b>it</b>", createdAt: "2026-10-19T11:00:00Z", editedAt: null },
      { commentId: "z", scopeKind: "run" as const, scopeId: "r1", parentId: null, author: { userId: "bob", name: null }, snippet: "not loaded", createdAt: "2026-10-19T11:00:00Z", editedAt: null },
      { commentId: "y", scopeKind: "run" as const, scopeId: "OTHER", parentId: null, author: { userId: "bob", name: null }, snippet: "elsewhere", createdAt: "2026-10-19T11:00:00Z", editedAt: null },
    ];
    const onShow = vi.fn();
    const props = { query: "it", hits, nextCursor: "y", busy: false, loadedIds: new Set(["a"]), scope: { kind: "run", id: "r1" }, now: NOW, onMore: vi.fn(), onShow };
    const markup = renderToStaticMarkup(<SearchResults {...props} />);
    expect(markup).toContain("found &lt;b&gt;it&lt;/b&gt;");
    expect(markup).toContain("Not loaded yet");
    expect(markup).toContain("in another run");
    expect(markup).toContain("More results");
    const tree = expand(SearchResults(props));
    press(button(tree, "Show in thread"));
    expect(onShow).toHaveBeenCalledWith("a");
    expect(findAll(tree, (n) => n.type === "button" && /Show in thread/.test(textOf(n)))).toHaveLength(1);
  });
});
