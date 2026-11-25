"use client";

// DiffPane — the right pane's diff: file header, per-file unified diff with
// line numbers, add/del tints. Data is the review service's recorded diff for
// the run's PR (getReviewData, same as the live review page). Loading, empty
// ("no PR yet"), error and binary/absence are all real states.

import * as React from "react";
import type { RunView } from "./useRun";
import { fileStatLabel, parseUnifiedDiff, type DiffLine } from "./previewData";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { Button } from "@/components/ui";
import type { DiffComment } from "./reviewFlow";

function LineNo({ n }: { n: number | null }) {
  return <span className="ws-diff-no">{n === null ? "" : n}</span>;
}

function DiffLineRow({ line }: { line: DiffLine }) {
  const numbered = line.tone !== "meta";
  return (
    <div className={`ws-diff-line ${line.tone}`}>
      <LineNo n={numbered ? line.oldNo : null} />
      <LineNo n={numbered ? line.newNo : null} />
      <code>{line.text || " "}</code>
    </div>
  );
}

function CommentForm({ path, onComment }: { path: string; onComment: (c: DiffComment) => Promise<void> }) {
  const [line, setLine] = React.useState("");
  const [body, setBody] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  return (
    <details className="ws-diff-comment">
      <summary>Comment on this file</summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!body.trim() || busy) return;
          setBusy(true);
          void onComment({ path, line: line ? Number(line) : null, body: body.trim() })
            .then(() => {
              setBody("");
              setLine("");
            })
            .catch(() => {}) // the pane shows the error; the text stays in the form
            .finally(() => setBusy(false));
        }}
      >
        <label>
          Line (optional)
          <input type="number" min={1} value={line} onChange={(e) => setLine(e.target.value)} />
        </label>
        <label>
          Comment
          <textarea rows={2} required value={body} onChange={(e) => setBody(e.target.value)} />
        </label>
        <Button type="submit" size="sm" disabled={busy || !body.trim()}>{busy ? "Sending…" : "Add comment"}</Button>
      </form>
    </details>
  );
}

export function DiffPane({ view, compact = false, onComment }: {
  view: RunView;
  compact?: boolean;
  /** When given, each file gets a comment form. */
  onComment?: (c: DiffComment) => Promise<void>;
}) {
  const { review, reviewState, agg, error, retry } = view;
  // ponytail: "reviewed" ticks live in this tab only; the review service has no per-file state.
  const [reviewed, setReviewed] = React.useState<ReadonlySet<string>>(new Set());

  if (!agg) return null;

  if (reviewState === "loading") {
    return <LoadingState label="Loading the diff…" rows={3} />;
  }
  if (reviewState === "error") {
    return <ErrorState title="The diff didn’t load" message={error ?? "The review service could not be reached."} onRetry={retry} />;
  }
  if (!review || !agg.prId) {
    return (
      <EmptyState
        title="No pull request yet"
        body="The diff appears here the moment the agent opens a pull request for this run."
      />
    );
  }
  const files = (review.changed_files ?? review.changedFiles) ?? [];
  const parsed = parseUnifiedDiff(review.diff ?? "");
  if (files.length === 0 && parsed.length === 0) {
    return (
      <EmptyState
        title="No textual changes recorded"
        body="The pull request exists but the review service recorded no textual diff for it."
      />
    );
  }

  const stats = files.map((f) => ({
    path: f.path,
    additions: f.additions,
    deletions: f.deletions,
    status: (f as { status?: string }).status ?? "changed",
  }));
  const statByPath = new Map(stats.map((f) => [f.path, fileStatLabel(f)]));

  return (
    <div className="ws-diff" data-testid="preview-diff">
      <p className="ws-diff-note" role="status" data-testid="diff-reviewed-count">
        {parsed.filter((f) => reviewed.has(f.path)).length} of {parsed.length} files marked reviewed
      </p>
      {parsed.map((file, i) => (
        <section key={`${file.path}-${i}`} className="ws-diff-file" aria-label={`Diff of ${file.path}`}>
          <h3 className="ws-diff-path">
            <code>{file.path}</code>
            {statByPath.get(file.path) ? (
              <span className="ws-diff-stat">{statByPath.get(file.path)}</span>
            ) : null}
          </h3>
          <label className="ws-diff-reviewed">
            <input
              type="checkbox"
              checked={reviewed.has(file.path)}
              onChange={(e) => {
                const next = new Set(reviewed);
                if (e.target.checked) next.add(file.path);
                else next.delete(file.path);
                setReviewed(next);
              }}
            />
            Reviewed (hides this file&rsquo;s diff)
          </label>
          {!reviewed.has(file.path) && (
            <div className={`ws-diff-code${compact ? " compact" : ""}`} tabIndex={0} aria-label={`Diff of ${file.path}, scrollable horizontally`}>
              {file.lines.map((line, j) => (
                <DiffLineRow key={j} line={line} />
              ))}
            </div>
          )}
          {onComment && <CommentForm path={file.path} onComment={onComment} />}
        </section>
      ))}
      <p className="ws-diff-note">This is the pull request&rsquo;s full diff.</p>
    </div>
  );
}
