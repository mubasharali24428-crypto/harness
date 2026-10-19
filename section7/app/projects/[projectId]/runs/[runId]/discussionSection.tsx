"use client";

// Part 43 UI, container half: fetches the comments of one scope, keeps the single open inline form,
// and performs the actions. Run page: <DiscussionSection scope={{kind:"run", id}} .../>. A session page
// does not exist in section7 yet; the same component takes {kind:"session", id} (session scope has no
// live events, so it polls every 15 s).
import * as React from "react";
import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { AnnounceContext } from "@/components/LiveAnnouncer";
import { Notice } from "@/components/ui/Notice";
import { scrollBehavior } from "@/lib/a11y";
import {
  type CommentView, type DiscussionPolicy, type Scope, type SearchHit,
  commentError, createComment, deleteComment, editComment, exportComments, getCommentHistory, listComments,
  moderateComment, promoteComment, reactComment, searchDiscussions,
} from "./discussionApi";
import { canSeeExport, exportFileName, mergeComments, searchReady } from "./discussionModel";
import { type CommentActions, type CommentsUi, type HistoryState, CommentsView, closedUi } from "./discussionView";
import { usePolled } from "./usePolled";

/** Fallback shown until the first page arrives (the server's own policy replaces it). */
const DEFAULT_POLICY: DiscussionPolicy = {
  editWindowSeconds: 900, retentionDays: 365, auditSensitive: "auto", maxReplyDepth: 2, maxBodyChars: 4000,
  reactions: ["thumbs_up", "thumbs_down", "heart", "eyes", "celebrate", "question", "check"], refKinds: [],
};

/** Browser-only: offer a JSON document as a file download. */
function downloadJson(name: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

export function DiscussionSection({ scope, token, me, role, canSteer, people, tick = 0 }: {
  scope: Scope; token: string; me: string; role?: string; canSteer: boolean; people: string[]; tick?: number;
}) {
  const announce = useContext(AnnounceContext);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [first, reload] = usePolled(useCallback(async () => {
    try { const r = await listComments(scope, token, { limit: 100 }); setLoadError(null); return r; } catch (e) { setLoadError(commentError(e)); throw e; }
  }, [scope.kind, scope.id, token]), tick, 15000);
  const [more, setMore] = useState<CommentView[]>([]);
  const [moreCursor, setMoreCursor] = useState<string | null | undefined>(undefined); // undefined: first page not exhausted yet
  const [ui, setUi] = useState<CommentsUi>(closedUi);
  const [histories, setHistories] = useState<Record<string, HistoryState>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [searchBusy, setSearchBusy] = useState(false);
  const [page, setPage] = useState<{ query: string; hits: SearchHit[]; nextCursor: string | null } | null>(null);
  const returnTo = useRef<string | null>(null);
  const known = useRef<Set<string> | null>(null);
  const root = useRef<HTMLDivElement | null>(null);

  const comments = mergeComments(first?.comments ?? [], more);
  const policy = first?.policy ?? DEFAULT_POLICY;
  const nextCursor = moreCursor !== undefined ? moreCursor : (first?.nextCursor ?? null);

  // Say (politely, throttled) when other people comment; the first load is silent.
  useEffect(() => {
    if (!first) return;
    const ids = new Set(first.comments.map((c) => c.id));
    // Run scope: the collaboration event stream already announces comment events; only sessions need this.
    if (known.current && announce && scope.kind === "session") {
      for (const c of first.comments) if (!known.current.has(c.id) && c.author.userId !== me && c.state === "visible") announce(`New comment from ${c.author.name || c.author.userId}`);
    }
    known.current = ids;
  }, [first, me, announce, scope.kind]);

  // Focus: opening a form moves focus into it, closing returns it to the button that opened it.
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    if (ui.open) {
      const target = el.querySelector<HTMLElement>(`[data-open-form="${ui.open.kind === "edit" || ui.open.kind === "reply" ? `${ui.open.kind}-${ui.open.id}` : ui.open.kind === "history" ? "history" : ui.open.kind === "moderate" ? `moderate-${ui.open.id}` : `${ui.open.kind}-${ui.open.id}`}"]`);
      target?.focus({ preventScroll: false });
    } else if (returnTo.current) {
      const back = el.querySelector<HTMLElement>(`[data-comment-action="${returnTo.current}"]`) ?? el.querySelector<HTMLElement>(`#comment-${returnTo.current.split(":")[0]}`);
      returnTo.current = null;
      back?.focus();
    }
  }, [ui.open?.id, ui.open?.kind]);

  const act = useCallback(async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true);
    try { await fn(); setError(null); if (done) setNotice(done); } catch (e) { setError(commentError(e)); } finally { setBusy(false); await reload(); }
  }, [reload]);

  const close = useCallback(() => setUi(closedUi), []);
  const actions: CommentActions = {
    open: (id, kind) => {
      returnTo.current = `${id}:${kind}`;
      setUi({ open: { id, kind } });
      if (kind === "history") {
        setHistories((h) => ({ ...h, [id]: { status: "loading" } }));
        getCommentHistory(scope, token, id).then(
          (data) => setHistories((h) => ({ ...h, [id]: { status: "ready", data } })),
          (e) => setHistories((h) => ({ ...h, [id]: { status: "error", message: commentError(e) } })));
      }
    },
    close,
    submitEdit: (c, body, mentions) => { void act(async () => { await editComment(scope, token, c.id, { body, expectedVersion: c.version, mentions }); close(); }); },
    submitReply: (parent, body, mentions) => { void act(async () => { await createComment(scope, token, { body, parentId: parent.id, mentions, requestId: crypto.randomUUID() }); close(); }); },
    react: (c, code, present) => { void act(() => reactComment(scope, token, c.id, code, present)); },
    remove: (c) => { void act(async () => { await deleteComment(scope, token, c.id); close(); }, "Comment deleted."); },
    promote: (c) => {
      void act(async () => {
        const r = await promoteComment(scope, token, c.id, { expectedVersion: c.version, runId: scope.kind === "run" ? scope.id : undefined });
        close();
        setNotice(r.queued
          ? "Sent as guidance, but someone else holds control: it is queued until they accept it."
          : r.duplicate ? "This comment was already sent as guidance." : "Sent to the agent as guidance. It picks it up the next time it starts or resumes.");
        announce?.("Comment sent as guidance");
      });
    },
    moderate: (c, action, reason) => { void act(async () => { await moderateComment(scope, token, c.id, action, reason); close(); }, `Comment ${action === "hide" ? "hidden" : action === "restore" ? "restored" : "deleted"}.`); },
  };

  const loadMore = () => {
    if (!nextCursor) return;
    void act(async () => {
      const r = await listComments(scope, token, { after: nextCursor, limit: 100 });
      setMore((m) => mergeComments(m, r.comments));
      setMoreCursor(r.nextCursor);
    });
  };
  const doSearch = (cursor?: string | null) => {
    if (!searchReady(q)) return;
    setSearchBusy(true);
    searchDiscussions(token, q.trim(), { before: cursor ?? undefined, limit: 20 }).then(
      (r) => { setError(null); setPage((prev) => ({ query: r.query, hits: cursor && prev ? [...prev.hits, ...r.results] : r.results, nextCursor: r.nextCursor })); },
      (e) => setError(commentError(e)),
    ).finally(() => setSearchBusy(false));
  };
  const show = (id: string) => {
    const el = root.current?.querySelector<HTMLElement>(`#comment-${CSS.escape(id)}`);
    el?.scrollIntoView({ block: "center", behavior: scrollBehavior() });
    el?.focus({ preventScroll: true });
  };

  if (!first && loadError) { // never loaded: say so instead of showing an empty panel with a form that cannot work
    return (
      <section aria-label="Discussion" data-testid="discussion-unavailable">
        <h3 style={{ fontSize: 13 }}>Discussion</h3>
        <Notice tone="warning" role="status">Discussion is not available right now: {loadError}</Notice>
      </section>
    );
  }
  return (
    <div ref={root}>
      <CommentsView
        comments={comments} policy={policy} me={me} canSteer={canSteer} canComment={role !== "guest"} people={people}
        busy={busy} error={error} notice={notice} ui={ui} histories={histories} hasMore={nextCursor !== null} loading={!first}
        scopeLabel={scope.kind} canExport={canSeeExport(role)} actions={actions}
        search={{ q, busy: searchBusy, page, scope }}
        onCreate={(body, mentions) => void act(() => createComment(scope, token, { body, mentions, requestId: crypto.randomUUID() }))}
        onLoadMore={loadMore}
        onSearchChange={setQ} onSearch={() => doSearch()} onSearchMore={() => doSearch(page?.hits[page.hits.length - 1]?.commentId)} onShow={show}
        onExport={() => void act(async () => { downloadJson(exportFileName(scope.kind, scope.id), await exportComments(scope, token)); }, "Export downloaded.")}
      />
    </div>
  );
}
