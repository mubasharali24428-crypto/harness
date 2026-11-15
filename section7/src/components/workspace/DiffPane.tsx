"use client";

// DiffPane — the right pane's diff: file header, per-file unified diff with
// line numbers, add/del tints. Data is the review service's recorded diff for
// the run's PR (getReviewData, same as the live review page). Loading, empty
// ("no PR yet"), error and binary/absence are all real states.

import * as React from "react";
import type { RunView } from "./useRun";
import { fileStatLabel, parseUnifiedDiff, type DiffLine } from "./previewData";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";

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

export function DiffPane({ view, compact = false }: { view: RunView; compact?: boolean }) {
  const { review, reviewState, agg, error, retry } = view;

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
      {parsed.map((file, i) => (
        <section key={`${file.path}-${i}`} className="ws-diff-file" aria-label={`Diff of ${file.path}`}>
          <h3 className="ws-diff-path">
            <code>{file.path}</code>
            {statByPath.get(file.path) ? (
              <span className="ws-diff-stat">{statByPath.get(file.path)}</span>
            ) : null}
          </h3>
          <div className={`ws-diff-code${compact ? " compact" : ""}`} tabIndex={0} aria-label={`Diff of ${file.path}, scrollable horizontally`}>
            {file.lines.map((line, j) => (
              <DiffLineRow key={j} line={line} />
            ))}
          </div>
        </section>
      ))}
      <p className="ws-diff-note">
        Since the last iteration is the default view once the agent posts a second
        revision; for now this is the pull request&rsquo;s full diff.
      </p>
    </div>
  );
}
