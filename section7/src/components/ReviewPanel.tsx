"use client";

// A4-S1 (F007 inline diff review, F009 multi-file curation): per-hunk accept/reject/comment.
// Backend denial or unavailability is shown as-is — never converted into a fake success.
import * as React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import type { DevWorkflowApi } from "@/lib/devWorkflowApi";
import {
  type ChangeSet,
  type Decision,
  decisionOf,
  flatHunks,
  keyIntent,
  moveIndex,
  summarize,
} from "@/lib/reviewModel";

const LINE_STYLE: Record<string, React.CSSProperties> = {
  add: { background: "rgba(46,160,67,0.18)" },
  del: { background: "rgba(248,81,73,0.18)" },
  ctx: {},
};
const VISUALLY_HIDDEN: React.CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
};
const PREFIX: Record<string, string> = { add: "+", del: "-", ctx: " " };

export function ReviewPanelView(props: {
  cs: ChangeSet;
  canWrite: boolean;
  busy: boolean;
  error: string | null;
  applied: string | null;
  focusedKey: string | null;
  onDecide: (path: string, hunkId: string, decision: Decision, comment?: string) => void;
  onApply: () => void;
  onCancel: () => void;
  onKeyDown?: (e: React.KeyboardEvent<HTMLDivElement>) => void;
  onFocusHunk?: (key: string) => void;
}) {
  const { cs, canWrite, busy, error, applied, focusedKey } = props;
  const summary = summarize(cs, canWrite);
  const total = flatHunks(cs).length;
  return (
    <section aria-labelledby="review-title" data-testid="review-panel" onKeyDown={props.onKeyDown}>
      <h2 id="review-title">{cs.title}</h2>
      <p aria-live="polite" data-testid="review-summary">
        {summary.accepted} accepted, {summary.rejected} rejected, {summary.pending} pending of {total} hunks
      </p>
      {summary.staleFiles.length > 0 && (
        <p role="alert" data-testid="review-stale">
          The workspace changed since this change set was composed ({summary.staleFiles.join(", ")}). Applying is blocked.
        </p>
      )}
      {error && (
        <p role="alert" data-testid="review-error">
          {error}
        </p>
      )}
      {applied && <p role="status" data-testid="review-applied">{applied}</p>}
      {cs.files.map((f) => (
        <div key={f.path} data-testid={`file-${f.path}`}>
          <h3>{f.path}</h3>
          <ul style={{ listStyle: "none", padding: 0 }}>
            {f.hunks.map((h, i) => {
              const key = `${f.path}#${h.id}`;
              const decision = decisionOf(f, h.id);
              const focused = focusedKey === key;
              return (
                <li
                  key={h.id}
                  role="group"
                  aria-label={`Hunk ${i + 1} of ${f.hunks.length} in ${f.path}`}
                  data-testid={`hunk-${key}`}
                  data-decision={decision}
                  tabIndex={0}
                  onFocus={() => props.onFocusHunk?.(key)}
                  style={{ outline: focused ? "2px solid var(--accent, #58a6ff)" : "none", marginBottom: 12 }}
                >
                  <pre style={{ margin: 0, fontFamily: "monospace", overflowX: "auto" }}>
                    {h.lines.map((l, n) => (
                      <div key={n} style={LINE_STYLE[l.kind]}>
                        {PREFIX[l.kind]}
                        {l.text}
                      </div>
                    ))}
                  </pre>
                  <div role="toolbar" aria-label={`Decide hunk ${i + 1} in ${f.path}`}>
                    {(["accept", "reject"] as const).map((d) => (
                      <button
                        key={d}
                        type="button"
                        aria-pressed={decision === d}
                        disabled={!canWrite || busy || cs.status !== "open"}
                        onClick={() => props.onDecide(f.path, h.id, decision === d ? "pending" : d)}
                      >
                        {d === "accept" ? "Accept" : "Reject"}
                      </button>
                    ))}
                    <label>
                      <span style={VISUALLY_HIDDEN}>Comment on hunk {i + 1}</span>
                      <input
                        type="text"
                        defaultValue={f.decisions[h.id]?.comment ?? ""}
                        placeholder="Comment"
                        disabled={!canWrite || busy || cs.status !== "open"}
                        onBlur={(e) => {
                          const v = e.currentTarget.value.trim();
                          if (v && v !== (f.decisions[h.id]?.comment ?? "")) props.onDecide(f.path, h.id, decision, v);
                        }}
                      />
                    </label>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      <div>
        <button type="button" onClick={props.onApply} disabled={!summary.canApply || busy} data-testid="review-apply">
          Apply accepted hunks
        </button>
        <button type="button" onClick={props.onCancel} disabled={!canWrite || busy || cs.status !== "open"}>
          Cancel change set
        </button>
        {summary.blocker && <p data-testid="review-blocker">{summary.blocker}</p>}
        <p>Keys: j/k move · a accept · r reject · u undo · Esc leave. Merging stays a human action in your Git provider.</p>
      </div>
    </section>
  );
}

export function ReviewPanel({ api, changeSetId, canWrite }: { api: DevWorkflowApi; changeSetId: string; canWrite: boolean }) {
  const [cs, setCs] = useState<ChangeSet | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [applied, setApplied] = useState<string | null>(null);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const idem = useRef(`apply-${Math.random().toString(36).slice(2)}`);

  const fail = useCallback((e: unknown) => {
    setError(e instanceof ApiError ? `${e.message} (${e.code})` : "Unexpected error");
  }, []);

  useEffect(() => {
    let live = true;
    api
      .getChangeSet(changeSetId)
      .then((v) => live && setCs(v))
      .catch((e) => live && fail(e));
    return () => {
      live = false;
    };
  }, [api, changeSetId, fail]);

  const hunks = useMemo(() => (cs ? flatHunks(cs) : []), [cs]);

  const decide = useCallback(
    async (path: string, hunkId: string, decision: Decision, comment?: string) => {
      if (!cs) return;
      setBusy(true);
      setError(null);
      try {
        // Server is authoritative: only reflect the decision after it accepted it.
        const next = await api.decide(cs.changeSetId, [{ path, hunkId, decision, comment }]);
        setCs(next);
      } catch (e) {
        fail(e);
        setCs((cur) => cur); // keep prior state; no optimistic success on denial
      } finally {
        setBusy(false);
      }
    },
    [api, cs, fail],
  );

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const t = e.target as HTMLElement;
    const intent = keyIntent({
      key: e.key,
      ctrlKey: e.ctrlKey,
      metaKey: e.metaKey,
      altKey: e.altKey,
      targetIsTextEntry: t.tagName === "INPUT" || t.tagName === "TEXTAREA",
    });
    if (!intent || !cs) return;
    const idx = hunks.findIndex((h) => `${h.path}#${h.hunkId}` === focusedKey);
    if (intent.type === "move") {
      const n = moveIndex(idx, intent.delta, hunks.length);
      if (n >= 0) {
        const k = `${hunks[n].path}#${hunks[n].hunkId}`;
        setFocusedKey(k);
        (document.querySelector(`[data-testid="hunk-${CSS.escape(k)}"]`) as HTMLElement | null)?.focus();
      }
      e.preventDefault();
    } else if (intent.type === "decide" && idx >= 0 && canWrite) {
      void decide(hunks[idx].path, hunks[idx].hunkId, intent.decision);
      e.preventDefault();
    } else if (intent.type === "blur") {
      (document.activeElement as HTMLElement | null)?.blur();
      setFocusedKey(null);
    }
  };

  if (!cs) {
    return error ? (
      <p role="alert" data-testid="review-error">{error}</p>
    ) : (
      <p role="status">Loading change set…</p>
    );
  }
  return (
    <ReviewPanelView
      cs={cs}
      canWrite={canWrite}
      busy={busy}
      error={error}
      applied={applied}
      focusedKey={focusedKey}
      onFocusHunk={setFocusedKey}
      onKeyDown={onKeyDown}
      onDecide={decide}
      onApply={async () => {
        setBusy(true);
        setError(null);
        try {
          const res = await api.apply(cs.changeSetId, idem.current);
          setApplied(`Applied: ${res.files.filter((f) => f.status === "written").length} file(s) written.`);
          setCs(await api.getChangeSet(cs.changeSetId));
        } catch (e) {
          fail(e);
        } finally {
          setBusy(false);
        }
      }}
      onCancel={async () => {
        setBusy(true);
        try {
          setCs(await api.cancel(cs.changeSetId));
        } catch (e) {
          fail(e);
        } finally {
          setBusy(false);
        }
      }}
    />
  );
}
