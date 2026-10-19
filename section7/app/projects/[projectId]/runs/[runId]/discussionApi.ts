// Part 43 client: comments on a run (and on a collaboration session) over the orchestrator routes in
// docs/discussion.md section 14. Same fetch helper as collabApi.ts (bearer token only; the server never
// trusts an actor in the body). Comment text is PLAIN TEXT: callers render it as text, never as HTML.
import { ApiError } from "@/lib/api";
import { PLAN_GATE_BASE } from "../../../../dashboard/planGateApi";
import { call, post } from "./collabApi";
import { mutationError } from "./realtimeCore";

export type ScopeKind = "run" | "session";
export interface Scope { kind: ScopeKind; id: string }

export interface ReactionCount { count: number; mine: boolean }
export interface CommentView {
  id: string; scopeKind: ScopeKind; scopeId: string; parentId: string | null; depth: number;
  author: { userId: string; name: string | null };
  /** null when the viewer may not read it (hidden, deleted, purged). */
  body: string | null;
  state: "visible" | "hidden" | "deleted"; purged: boolean;
  createdAt: string; editedAt: string | null; version: number; edited: boolean;
  ref: { kind: string; id?: string; pos?: number | null; available: boolean } | null;
  reactions: Record<string, ReactionCount>;
  mentions: { userId: string; name: string }[];
  moderation: { state: string; by: string; at: string | null; reason: string | null } | null;
  deleted: { at: string | null; by: string | null } | null;
  promotion: { runId: string; by: string; at: string | null; commentVersion: number; seq: number | null; instructionId: string | null } | null;
  editableUntil: string | null;
  can: { edit: boolean; delete: boolean; react: boolean; reply: boolean; promote: boolean; moderate: boolean };
}
export interface DiscussionPolicy {
  editWindowSeconds: number; retentionDays: number; auditSensitive: string; maxReplyDepth: number;
  maxBodyChars: number; reactions: string[]; refKinds: string[];
}
export interface CommentsPage { scope: Scope; comments: CommentView[]; nextCursor: string | null; policy: DiscussionPolicy }
export interface CommentRevision { version: number; editedBy: string; editedAt: string | null; body: string | null }
export interface CommentHistory {
  commentId: string; state: string; purged: boolean; author: { userId: string; name: string | null };
  createdAt: string; currentVersion: number; revisions: CommentRevision[];
}
export interface SearchHit {
  commentId: string; scopeKind: ScopeKind; scopeId: string; parentId: string | null;
  author: { userId: string; name: string | null }; snippet: string; createdAt: string; editedAt: string | null;
}
export interface SearchPage { query: string; results: SearchHit[]; nextCursor: string | null }

// ---- stable error codes -> plain language (the server message is the fallback) ----
const MESSAGES: Record<string, string> = {
  ROLE_DENIED: "Your role cannot do that here.",
  NOT_AUTHOR: "Only the person who wrote this comment can do that.",
  NOT_PARTICIPANT: "Join the run before you send a comment as guidance.",
  NOT_FOUND: "This run or session is not available to you.",
  COMMENT_NOT_FOUND: "That comment no longer exists.",
  PILOT_NOT_ENABLED: "Discussion is not enabled for your team yet.",
  BAD_BODY: "Write something first. Comments are plain text, up to the length shown.",
  BAD_PARENT: "The comment you are replying to is not available.",
  REPLY_TOO_DEEP: "Replies can only nest a few levels. Reply to the thread above instead.",
  BAD_REF: "That reference is not available.",
  BAD_PATHS: "That file path is not valid.",
  BAD_RUN: "Name a run of this session to receive the guidance.",
  BAD_CURSOR: "The list moved. Reload the comments.",
  BAD_QUERY: "Type at least two characters to search.",
  REASON_REQUIRED: "Give a reason of at least three characters.",
  PARENT_UNAVAILABLE: "The comment you are replying to was hidden or removed.",
  SCOPE_READ_ONLY: "This discussion is read-only now.",
  EDIT_WINDOW_CLOSED: "The time to edit this comment has passed.",
  COMMENT_UNAVAILABLE: "This comment was hidden, deleted or expired, so that is not possible.",
  COMMENT_DELETED: "This comment was deleted and cannot be restored.",
  DISCUSSION_UNAVAILABLE: "Discussion is temporarily unavailable. Try again shortly.",
  SESSION_FULL: "This run is full.",
  LEASE_HELD: "Someone else holds control, so your guidance was not sent directly.",
};

export function commentError(e: unknown): string {
  return mutationError(e, (x) => {
    if (x instanceof ApiError) return MESSAGES[x.code] ?? x.message;
    return String((x as Error)?.message ?? x);
  });
}

// ---- calls ----
const orch = `${PLAN_GATE_BASE}/api/v1/orchestrator`;
const base = (s: Scope) => `${orch}/${s.kind === "run" ? "runs" : "sessions"}/${encodeURIComponent(s.id)}/comments`;
const one = (s: Scope, id: string) => `${base(s)}/${encodeURIComponent(id)}`;

export const listComments = (s: Scope, t: string, o: { after?: string | null; limit?: number } = {}) => {
  const q = new URLSearchParams({ limit: String(o.limit ?? 50) });
  if (o.after) q.set("after", o.after);
  return call<CommentsPage>(`${base(s)}?${q}`, t);
};
export const createComment = (
  s: Scope, t: string,
  b: { body: string; parentId?: string | null; mentions?: string[]; requestId?: string },
) => post<CommentView & { duplicate: boolean; ignoredMentions: number }>(base(s), t, {
  body: b.body, parentId: b.parentId ?? undefined, mentions: b.mentions ?? [], requestId: b.requestId,
});
export const editComment = (s: Scope, t: string, id: string, b: { body: string; expectedVersion: number; mentions?: string[] }) =>
  call<CommentView & { changed: boolean }>(one(s, id), t, { method: "PATCH", body: JSON.stringify(b) });
export const deleteComment = (s: Scope, t: string, id: string) =>
  call<{ id: string; state: string; changed: boolean }>(one(s, id), t, { method: "DELETE" });
/** A set operation (idempotent): `present` says whether the reaction should exist. */
export const reactComment = (s: Scope, t: string, id: string, reaction: string, present: boolean) =>
  post<{ id: string; reactions: Record<string, ReactionCount> }>(`${one(s, id)}/reactions`, t, { reaction, present });
/** The explicit step to agent guidance. `expectedVersion` is the version the person read. */
export const promoteComment = (s: Scope, t: string, id: string, b: { expectedVersion: number; runId?: string; reason?: string }) =>
  post<{ commentId: string; runId: string; seq: number | null; instructionId: string | null; queued: boolean; commentVersion: number; duplicate: boolean }>(
    `${one(s, id)}/promote`, t, { action: "send_as_guidance", ...b });
export const moderateComment = (s: Scope, t: string, id: string, action: "hide" | "restore" | "delete", reason: string) =>
  post<{ id: string; state: string; changed: boolean }>(`${one(s, id)}/moderate`, t, { action, reason });
export const getCommentHistory = (s: Scope, t: string, id: string) => call<CommentHistory>(`${one(s, id)}/history`, t);
export const exportComments = (s: Scope, t: string) => call<{ format: string; count: number; truncated: boolean; comments: unknown[] }>(`${base(s)}/export`, t);
export const searchDiscussions = (t: string, q: string, o: { scopeKind?: ScopeKind; before?: string | null; limit?: number } = {}) => {
  const p = new URLSearchParams({ q });
  if (o.scopeKind) p.set("scopeKind", o.scopeKind);
  if (o.before) p.set("before", o.before);
  if (o.limit) p.set("limit", String(o.limit));
  return call<SearchPage>(`${orch}/discussions/search?${p}`, t);
};
