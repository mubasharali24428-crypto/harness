"use client";

// Part 43 UI, presentational half: threaded comments with edit / delete / react / reply / promote,
// history viewer, mentions and search. Everything here is props-driven (static-render and element-tree
// tested); DiscussionSection (discussionSection.tsx) fetches and acts. Comment text is rendered as plain
// text (React escapes it) and never as HTML. Only ONE inline form is open at a time (`ui.open`), which keeps
// focus handling simple: opening moves focus into the form, closing returns it to the button that opened it.
import * as React from "react";
import { useState } from "react";
import { Button } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { Pill } from "@/components/ui";
import { formatDateTime, formatNumber, formatRelative, formatTime } from "@/lib/intl";
import type { CommentHistory, CommentView, DiscussionPolicy, SearchHit } from "./discussionApi";
import {
  type ThreadNode, authorLabel, bodyProblem, bodyView, buildThreads, keyIntent, moderationLine, parseMentions, reactionChips,
} from "./discussionModel";

const muted = { color: "var(--muted)" } as const;
const h3 = { fontSize: 13 } as const;

export type OpenKind = "edit" | "reply" | "promote" | "delete" | "history" | "moderate";
export interface CommentsUi { open: { id: string; kind: OpenKind } | null }
export const closedUi: CommentsUi = { open: null };

export type HistoryState = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; data: CommentHistory };

export interface CommentActions {
  open: (id: string, kind: OpenKind) => void;
  close: () => void;
  submitEdit: (c: CommentView, body: string, mentions: string[]) => void;
  submitReply: (parent: CommentView, body: string, mentions: string[]) => void;
  react: (c: CommentView, code: string, present: boolean) => void;
  remove: (c: CommentView) => void;
  promote: (c: CommentView) => void;
  moderate: (c: CommentView, action: "hide" | "restore" | "delete", reason: string) => void;
}

// ---- composer (has local text state; its pure parts are tested in discussionModel) ----

export function Composer(p: {
  label: string; initial?: string; submitLabel: string; policy: Pick<DiscussionPolicy, "maxBodyChars">;
  people: string[]; me: string; busy: boolean; autoFocusField?: boolean; formKey: string;
  onSubmit: (body: string, mentions: string[]) => void; onCancel?: () => void;
}) {
  const [text, setText] = useState(p.initial ?? "");
  const ids = React.useId();
  const problem = bodyProblem(text, p.policy);
  const mentions = parseMentions(text, p.people, p.me);
  const send = () => { if (!problem && !p.busy) { p.onSubmit(text.trim(), mentions); if (!p.onCancel) setText(""); } };
  return (
    <form
      aria-label={p.label}
      data-testid={`composer-${p.formKey}`}
      onSubmit={(e) => { e.preventDefault(); send(); }}
      style={{ marginTop: 6 }}
    >
      <textarea
        aria-label={p.label} rows={3} value={text} data-open-form={p.autoFocusField ? p.formKey : undefined}
        aria-invalid={(problem !== null && text.length > 0) || undefined}
        aria-describedby={`${ids}-hint`}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          const intent = keyIntent(e);
          if (intent === "submit") { e.preventDefault(); send(); }
          else if (intent === "cancel" && p.onCancel) { e.preventDefault(); p.onCancel(); }
        }}
        style={{ display: "block", width: "100%", margin: "4px 0" }}
      />
      <p id={`${ids}-hint`} style={{ ...muted, margin: "0 0 4px", fontSize: 12 }}>
        Plain text, {formatNumber(text.trim().length)} of {formatNumber(p.policy.maxBodyChars)} characters.
        {" "}Type @ and a participant's id to mention them.
        {mentions.length > 0 && <> They will be notified: <strong>{mentions.join(", ")}</strong>.</>}
        {" "}Ctrl+Enter sends{p.onCancel ? ", Escape cancels" : ""}.
      </p>
      <Button type="submit" size="sm" variant="primary" disabled={p.busy || problem !== null}>{p.submitLabel}</Button>
      {p.onCancel && <>{" "}<Button size="sm" onClick={p.onCancel}>Cancel</Button></>}
      {problem && text.length > 0 && <span role="status" style={{ ...muted, marginLeft: 8 }}>{problem}</span>}
    </form>
  );
}

// ---- history viewer ----

export function HistoryViewer({ state, now, onClose }: { state: HistoryState; now?: number; onClose: () => void }) {
  return (
    <section aria-label="Edit history" data-testid="comment-history" tabIndex={-1} data-open-form="history"
      onKeyDown={(e) => { if (keyIntent(e) === "cancel") { e.preventDefault(); onClose(); } }}
      style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 8, marginTop: 6 }}>
      <h4 style={{ ...h3, margin: "0 0 4px" }}>Edit history</h4>
      {state.status === "loading" && <p style={muted}>Loading history…</p>}
      {state.status === "error" && <Notice tone="danger" role="alert">{state.message}</Notice>}
      {state.status === "ready" && (
        state.data.purged
          ? <p style={muted}>The text was removed after the retention period.</p>
          : <ol style={{ margin: 0, paddingLeft: 20 }}>
              {state.data.revisions.map((r) => (
                <li key={r.version}>
                  <span style={muted}>
                    {r.version === 1 ? "Original" : `Version ${r.version}`} by {r.editedBy},{" "}
                    <time dateTime={r.editedAt ?? undefined} title={formatDateTime(r.editedAt)}>{formatRelative(r.editedAt, now)}</time>
                  </span>
                  {r.body === null
                    ? <p style={muted}>You cannot see this text.</p>
                    : <p style={{ margin: "2px 0 6px", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{r.body}</p>}
                </li>
              ))}
            </ol>)}
      <Button size="sm" onClick={onClose}>Close history</Button>
    </section>
  );
}

// ---- one comment ----

function Confirm(p: {
  id: string; label: string; busy: boolean; yes: string; yesVariant?: "primary" | "danger"; onYes: () => void; onCancel: () => void; children: React.ReactNode;
}) {
  return (
    <div role="group" aria-label={p.label} tabIndex={-1} data-open-form={p.id} data-testid={`confirm-${p.id}`}
      onKeyDown={(e) => { if (keyIntent(e) === "cancel") { e.preventDefault(); p.onCancel(); } }}
      style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 8, marginTop: 6 }}>
      <p style={{ margin: "0 0 6px" }}>{p.children}</p>
      <Button size="sm" variant={p.yesVariant ?? "primary"} disabled={p.busy} onClick={p.onYes}>{p.yes}</Button>{" "}
      <Button size="sm" onClick={p.onCancel}>Cancel</Button>
    </div>
  );
}

export function ModerateForm(p: { c: CommentView; busy: boolean; onModerate: CommentActions["moderate"]; onCancel: () => void }) {
  const [reason, setReason] = useState("");
  const ok = reason.trim().length >= 3;
  const hidden = p.c.state === "hidden";
  return (
    <div role="group" aria-label="Moderate this comment" tabIndex={-1} data-open-form={`moderate-${p.c.id}`}
      onKeyDown={(e) => { if (keyIntent(e) === "cancel") { e.preventDefault(); p.onCancel(); } }}
      style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 8, marginTop: 6 }}>
      <label>Reason (required, at least 3 characters){" "}
        <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} aria-required="true" style={{ width: "100%" }} />
      </label>
      <div style={{ marginTop: 6 }}>
        {hidden
          ? <Button size="sm" disabled={p.busy || !ok} onClick={() => p.onModerate(p.c, "restore", reason.trim())}>Restore</Button>
          : <Button size="sm" disabled={p.busy || !ok} onClick={() => p.onModerate(p.c, "hide", reason.trim())}>Hide from others</Button>}{" "}
        <Button size="sm" variant="danger" disabled={p.busy || !ok} onClick={() => p.onModerate(p.c, "delete", reason.trim())}>Delete</Button>{" "}
        <Button size="sm" onClick={p.onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

export function CommentCard(p: {
  c: CommentView; me: string; canSteer: boolean; canComment: boolean; policy: DiscussionPolicy; people: string[]; busy: boolean;
  ui: CommentsUi; history?: HistoryState; now?: number; actions: CommentActions; children?: React.ReactNode;
}) {
  const { c, actions: a } = p;
  const who = authorLabel(c, p.me);
  const body = bodyView(c);
  const mod = moderationLine(c);
  const open = p.ui.open?.id === c.id ? p.ui.open.kind : null;
  const tombstone = c.state === "deleted" || c.purged;
  const live = c.state === "visible" && !c.purged;
  const btn = (kind: OpenKind) => ({ "data-comment-action": `${c.id}:${kind}` }) as const;
  const aboutId = `about-${c.id}`;
  return (
    <li id={`comment-${c.id}`} data-testid={`comment-${c.id}`} tabIndex={-1} data-focus-park="" style={{ margin: "0 0 8px", listStyle: "none" }}>
      <article aria-label={`${c.depth > 0 ? "Reply" : "Comment"} by ${who}`} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 8 }}>
        <header id={aboutId} style={{ fontSize: 13 }}>
          <strong>{who}</strong>{" "}
          <time dateTime={c.createdAt} title={formatDateTime(c.createdAt)} style={muted}>{formatRelative(c.createdAt, p.now)}</time>
          {c.edited && !tombstone && (
            <span style={muted}>, edited <time dateTime={c.editedAt ?? undefined} title={formatDateTime(c.editedAt)}>{formatRelative(c.editedAt, p.now)}</time></span>
          )}{" "}
          {c.state === "hidden" && <Pill tone="yellow">Hidden</Pill>}
          {c.state === "deleted" && <Pill tone="red">Deleted</Pill>}
          {c.promotion && <Pill tone="blue">Sent as guidance</Pill>}
        </header>
        {body.kind === "text"
          ? <p data-testid={`body-${c.id}`} style={{ margin: "4px 0", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{body.text}</p>
          : <p data-testid={`body-${c.id}`} style={{ ...muted, margin: "4px 0", fontStyle: "italic" }}>{body.text}</p>}
        {body.kind === "text" && body.note && <p style={{ ...muted, margin: "0 0 4px" }}>{body.note}</p>}
        {mod && <p style={{ ...muted, margin: "0 0 4px" }}>{mod}</p>}
        {c.ref && (c.ref.available
          ? <p style={{ ...muted, margin: "0 0 4px" }}>About: {c.ref.kind}{c.ref.id ? ` ${c.ref.id}` : ""}{c.ref.pos != null ? `, line ${formatNumber(c.ref.pos)}` : ""}</p>
          : <p style={{ ...muted, margin: "0 0 4px" }}>The {c.ref.kind} this refers to is no longer available.</p>)}
        {c.mentions.length > 0 && !tombstone && <p style={{ ...muted, margin: "0 0 4px" }}>Mentioned: {c.mentions.map((m) => m.name).join(", ")}</p>}
        {c.promotion && (
          <p data-testid={`promoted-${c.id}`} style={{ ...muted, margin: "0 0 4px" }}>
            {c.promotion.by} sent version {c.promotion.commentVersion} to the agent as guidance{c.promotion.seq === null ? " (waiting for the person in control to accept)" : ""}.
          </p>
        )}

        {live && (
          <div role="group" aria-label={`Reactions to the comment by ${who}`} style={{ display: "flex", gap: 4, flexWrap: "wrap", margin: "4px 0" }}>
            {reactionChips(c, p.policy).map((r) => (
              <Button key={r.code} size="sm" disabled={p.busy || !c.can.react} aria-pressed={r.mine}
                aria-label={`${r.name}, ${r.count} ${r.count === 1 ? "person" : "people"}${r.mine ? ", including you" : ""}`}
                variant={r.mine ? "primary" : "secondary"}
                onClick={() => a.react(c, r.code, !r.mine)}>
                {r.name}{r.count > 0 ? ` ${formatNumber(r.count)}` : ""}
              </Button>
            ))}
          </div>
        )}

        <div role="group" aria-label={`Actions for the comment by ${who}`} style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
          {c.can.reply && p.canComment && <Button size="sm" disabled={p.busy} aria-describedby={aboutId} {...btn("reply")} onClick={() => a.open(c.id, "reply")}>Reply</Button>}
          {c.can.edit && <Button size="sm" disabled={p.busy} aria-describedby={aboutId} {...btn("edit")} onClick={() => a.open(c.id, "edit")}>Edit</Button>}
          {c.can.delete && <Button size="sm" variant="danger" disabled={p.busy} aria-describedby={aboutId} {...btn("delete")} onClick={() => a.open(c.id, "delete")}>Delete</Button>}
          {p.canSteer && c.can.promote && <Button size="sm" disabled={p.busy} aria-describedby={aboutId} {...btn("promote")} onClick={() => a.open(c.id, "promote")}>Send as guidance</Button>}
          {c.edited && !tombstone && <Button size="sm" aria-describedby={aboutId} aria-expanded={open === "history"} {...btn("history")} onClick={() => (open === "history" ? a.close() : a.open(c.id, "history"))}>History</Button>}
          {c.can.moderate && <Button size="sm" disabled={p.busy} aria-describedby={aboutId} {...btn("moderate")} onClick={() => a.open(c.id, "moderate")}>Moderate</Button>}
        </div>
        {c.can.edit && c.editableUntil && open !== "edit" && (
          <p style={{ ...muted, margin: "4px 0 0", fontSize: 12 }}>You can edit this until <time dateTime={c.editableUntil}>{formatTime(c.editableUntil)}</time>.</p>
        )}

        {open === "edit" && (
          <Composer formKey={`edit-${c.id}`} label={`Edit your comment`} initial={c.body ?? ""} submitLabel="Save changes" autoFocusField
            policy={p.policy} people={p.people} me={p.me} busy={p.busy}
            onSubmit={(b, m) => a.submitEdit(c, b, m)} onCancel={a.close} />
        )}
        {open === "reply" && (
          <Composer formKey={`reply-${c.id}`} label={`Reply to ${who}`} submitLabel="Send reply" autoFocusField
            policy={p.policy} people={p.people} me={p.me} busy={p.busy}
            onSubmit={(b, m) => a.submitReply(c, b, m)} onCancel={a.close} />
        )}
        {open === "delete" && (
          <Confirm id={`delete-${c.id}`} label="Confirm delete" busy={p.busy} yes="Delete comment" yesVariant="danger" onYes={() => a.remove(c)} onCancel={a.close}>
            Delete this comment? Its place in the thread stays as a note that it was deleted. Replies stay.
          </Confirm>
        )}
        {open === "promote" && (
          <Confirm id={`promote-${c.id}`} label="Confirm send as guidance" busy={p.busy} yes="Send as guidance" onYes={() => a.promote(c)} onCancel={a.close}>
            Send this comment to the agent as guidance? The agent receives the text exactly as it reads now (version {c.version}); editing it later changes nothing.
            It is sent once and cannot be taken back. If someone else holds control, it waits for them to accept first.
          </Confirm>
        )}
        {open === "history" && <HistoryViewer state={p.history ?? { status: "loading" }} now={p.now} onClose={a.close} />}
        {open === "moderate" && <ModerateForm c={c} busy={p.busy} onModerate={a.moderate} onCancel={a.close} />}
      </article>
      {p.children}
    </li>
  );
}

// ---- the thread list ----

/** Nested lists, one level per reply depth; indentation stops at the policy's maximum depth. */
export function ThreadList(p: {
  nodes: ThreadNode[]; render: (c: CommentView, replies: React.ReactNode) => React.ReactNode; maxDepth: number; level?: number;
}) {
  const level = p.level ?? 0;
  return (
    <ul aria-label={level === 0 ? "Comments" : "Replies"} style={{ margin: 0, padding: 0, paddingLeft: level === 0 ? 0 : 16, listStyle: "none" }}>
      {p.nodes.map((n) => p.render(n.c, n.children.length > 0
        ? <ThreadList nodes={n.children} render={p.render} maxDepth={p.maxDepth} level={Math.min(level + 1, p.maxDepth)} />
        : null))}
    </ul>
  );
}

// ---- search ----

export function SearchResults(p: {
  query: string; hits: SearchHit[]; nextCursor: string | null; busy: boolean; loadedIds: Set<string>; scope: { kind: string; id: string };
  now?: number; onMore: () => void; onShow: (commentId: string) => void;
}) {
  return (
    <section aria-label="Search results" data-testid="search-results">
      <h4 style={{ ...h3, margin: "6px 0 2px" }}>{p.hits.length === 0 ? `No matches for "${p.query}"` : `Matches for "${p.query}"`}</h4>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {p.hits.map((h) => {
          const here = h.scopeKind === p.scope.kind && h.scopeId === p.scope.id;
          return (
            <li key={h.commentId} data-testid={`hit-${h.commentId}`}>
              <strong>{h.author.name || h.author.userId}</strong>{" "}
              <time dateTime={h.createdAt} title={formatDateTime(h.createdAt)} style={muted}>{formatRelative(h.createdAt, p.now)}</time>{" "}
              <span style={muted}>in {here ? "this " : "another "}{h.scopeKind}</span>
              <p style={{ margin: "2px 0", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{h.snippet}</p>
              {here && (p.loadedIds.has(h.commentId)
                ? <Button size="sm" onClick={() => p.onShow(h.commentId)}>Show in thread</Button>
                : <span style={muted}>Not loaded yet: load more comments to see it in place.</span>)}
            </li>
          );
        })}
      </ul>
      {p.nextCursor && <Button size="sm" disabled={p.busy} onClick={p.onMore}>More results</Button>}
    </section>
  );
}

// ---- the panel ----

export function CommentsView(p: {
  comments: CommentView[]; policy: DiscussionPolicy; me: string; canSteer: boolean; canComment: boolean; people: string[];
  busy: boolean; error: string | null; notice: string | null; ui: CommentsUi; histories: Record<string, HistoryState>;
  hasMore: boolean; loading: boolean; scopeLabel: string; canExport: boolean; now?: number;
  search: { q: string; busy: boolean; page: { query: string; hits: SearchHit[]; nextCursor: string | null } | null; scope: { kind: string; id: string } };
  actions: CommentActions;
  onCreate: (body: string, mentions: string[]) => void;
  onLoadMore: () => void;
  onSearchChange: (q: string) => void; onSearch: () => void; onSearchMore: () => void; onShow: (id: string) => void;
  onExport: () => void;
}) {
  const threads = buildThreads(p.comments);
  const ids = new Set(p.comments.map((c) => c.id));
  const total = p.comments.length;
  return (
    <section aria-label="Discussion" data-testid="discussion">
      <h3 style={h3}>Discussion ({formatNumber(total)}{p.hasMore ? "+" : ""})</h3>
      <p style={muted}>
        Comments are for people. They are never sent to the agent unless someone with permission chooses &quot;Send as guidance&quot; on one.
      </p>
      {p.error && <Notice tone="danger" role="alert">{p.error}</Notice>}
      {p.notice && <Notice tone="info" role="status">{p.notice}</Notice>}

      <form role="search" aria-label={`Search ${p.scopeLabel} discussions`} onSubmit={(e) => { e.preventDefault(); p.onSearch(); }} style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
        <input type="search" aria-label="Search comments" placeholder="Search comments (2+ characters)" value={p.search.q} onChange={(e) => p.onSearchChange(e.target.value)} maxLength={100} style={{ flex: "1 1 200px" }} />
        <Button type="submit" size="sm" disabled={p.search.busy || p.search.q.trim().length < 2}>Search</Button>
        {p.canExport && <Button size="sm" disabled={p.busy} onClick={p.onExport}>Export discussion (JSON)</Button>}
      </form>
      {p.search.page && (
        <SearchResults query={p.search.page.query} hits={p.search.page.hits} nextCursor={p.search.page.nextCursor} busy={p.search.busy}
          loadedIds={ids} scope={p.search.scope} now={p.now} onMore={p.onSearchMore} onShow={p.onShow} />
      )}

      {total === 0 && !p.loading && <p style={muted}>No comments yet.</p>}
      {total > 0 && (
        <ThreadList nodes={threads} maxDepth={p.policy.maxReplyDepth}
          render={(c, replies) => (
            <CommentCard key={c.id} c={c} me={p.me} canSteer={p.canSteer} canComment={p.canComment} policy={p.policy} people={p.people}
              busy={p.busy} ui={p.ui} history={p.histories[c.id]} now={p.now} actions={p.actions}>{replies}</CommentCard>
          )} />
      )}
      {p.hasMore && <p><Button size="sm" disabled={p.busy || p.loading} onClick={p.onLoadMore}>Load more comments</Button></p>}

      {p.canComment
        ? <Composer formKey="new" label="Add a comment" submitLabel="Comment" policy={p.policy} people={p.people} me={p.me} busy={p.busy} onSubmit={p.onCreate} />
        : <p style={muted}>Your role can read the discussion but not write in it.</p>}
    </section>
  );
}
