// Part 43 view-model: pure helpers for the comments panel (threads, what to show for each comment state,
// mentions, reactions, export name). Comment text is plain text everywhere; nothing here builds markup.
import type { CommentView, DiscussionPolicy } from "./discussionApi";

export interface ThreadNode { c: CommentView; children: ThreadNode[] }

/** The API list is flat and chronological with `parentId`/`depth`; build the tree. A reply whose parent is
 *  not in the loaded page (older page, other cursor) is shown as a root instead of being dropped. */
export function buildThreads(comments: CommentView[]): ThreadNode[] {
  const nodes = new Map(comments.map((c) => [c.id, { c, children: [] as ThreadNode[] }]));
  const roots: ThreadNode[] = [];
  for (const n of nodes.values()) {
    const parent = n.c.parentId ? nodes.get(n.c.parentId) : undefined;
    (parent && parent !== n ? parent.children : roots).push(n);
  }
  return roots;
}

/** Merge a freshly fetched page into what is loaded: replace by id, append new ones, keep order by createdAt then id. */
export function mergeComments(prev: CommentView[], next: CommentView[]): CommentView[] {
  const byId = new Map(prev.map((c) => [c.id, c]));
  for (const c of next) byId.set(c.id, c);
  return [...byId.values()].sort((a, b) => (a.createdAt === b.createdAt ? (a.id < b.id ? -1 : 1) : a.createdAt < b.createdAt ? -1 : 1));
}

export type BodyView =
  | { kind: "text"; text: string; note: string | null }
  | { kind: "placeholder"; text: string };

const who = (c: CommentView) => c.author.name || c.author.userId;

/** What the body area shows: the text, or an honest placeholder for tombstones, hidden and expired comments. */
export function bodyView(c: CommentView): BodyView {
  if (c.state === "deleted") {
    const by = c.deleted?.by && c.deleted.by !== c.author.userId ? ` by ${c.deleted.by}` : "";
    return { kind: "placeholder", text: `This comment was deleted${by}.` };
  }
  if (c.purged) return { kind: "placeholder", text: "This comment was removed after the retention period." };
  if (c.state === "hidden") {
    if (c.body === null) return { kind: "placeholder", text: "A moderator hid this comment." };
    return { kind: "text", text: c.body, note: "Hidden from everyone except you and moderators." };
  }
  return { kind: "text", text: c.body ?? "", note: null };
}

/** One line for the moderation state (hidden / restored), or null. */
export function moderationLine(c: CommentView): string | null {
  const m = c.moderation;
  if (!m) return null;
  const verb = m.state === "hidden" ? "Hidden" : m.state === "deleted" ? "Removed" : "Restored";
  return `${verb} by ${m.by}${m.reason ? `: ${m.reason}` : ""}`;
}

export const authorLabel = (c: CommentView, me: string) => `${who(c)}${c.author.userId === me ? " (you)" : ""}`;

// ---- reactions ----
const NAMES: Record<string, string> = {
  thumbs_up: "Thumbs up", thumbs_down: "Thumbs down", heart: "Heart", eyes: "Eyes", celebrate: "Celebrate", question: "Question", check: "Check",
};
export const reactionName = (code: string) => NAMES[code] ?? code.replace(/_/g, " ");

/** The closed set comes from the API (`policy.reactions`); every one is offered, with its count and whether it is yours. */
export function reactionChips(c: CommentView, policy: Pick<DiscussionPolicy, "reactions">) {
  return policy.reactions.map((code) => {
    const r = c.reactions[code];
    return { code, name: reactionName(code), count: r?.count ?? 0, mine: r?.mine ?? false };
  });
}

// ---- mentions (explicit user ids; the server never parses text) ----
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** `@userId` tokens in the text that name a known participant. Duplicates and yourself are dropped; at most 10. */
export function parseMentions(body: string, candidates: string[], me: string): string[] {
  const out: string[] = [];
  for (const id of [...candidates].sort((a, b) => b.length - a.length)) {
    if (id === me || out.includes(id)) continue;
    if (new RegExp(`(^|[\\s(,])@${esc(id)}(?![\\w-])`).test(body)) out.push(id);
  }
  return out.slice(0, 10);
}

export function bodyProblem(body: string, policy: Pick<DiscussionPolicy, "maxBodyChars">): string | null {
  if (!body.trim()) return "Write something first.";
  if (body.trim().length > policy.maxBodyChars) return `Comments are limited to ${policy.maxBodyChars} characters.`;
  return null;
}

// ---- keyboard intent for inline forms: Ctrl/Cmd+Enter sends, Escape cancels ----
export function keyIntent(e: { key: string; ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean }): "submit" | "cancel" | null {
  if (e.key === "Escape") return "cancel";
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) return "submit";
  return null;
}

// ---- who may see which control (presentation only: every action is re-authorized by the server) ----
export const canSeeExport = (role: string | undefined) => role === "admin" || role === "engineer";

export function exportFileName(scopeKind: string, scopeId: string, now = new Date()): string {
  const safe = scopeId.replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 40);
  return `discussion-${scopeKind}-${safe}-${now.toISOString().slice(0, 10)}.json`;
}

/** Search needs two characters (the server says 422 BAD_QUERY otherwise). */
export const searchReady = (q: string) => q.trim().length >= 2;
