"use client";

// R4: the Diff pane's default view — the hunks of the run's last iteration
// (newest checkpoint), with the same per-hunk revert as the snapshot history.
// Presentational only (IterationDiffScope owns the data), so the node-env
// tests render it with react-dom/server.

import * as React from "react";
import type { CheckpointFile } from "@/lib/api";
import { hunkKey, hunkLabel, lineTone, summaryLine } from "@/lib/hunkReview";
import { iterationSourceNote, type IterationPick } from "@/lib/sinceLastIteration";
import { formatRelativeTime } from "@/lib/format";
import { EmptyState, ErrorState } from "@/components/States";
import { Button } from "@/components/ui";

export interface SinceLastIterationDiffProps {
  pick: IterationPick;
  files: CheckpointFile[];
  error: string | null;
  selected: ReadonlySet<string>;
  busy: boolean;
  notice: { tone: "success" | "danger"; text: string } | null;
  compact?: boolean;
  onToggle: (key: string) => void;
  onRevert: () => void;
  onRetry: () => void;
}

export function SinceLastIterationDiff(p: SinceLastIterationDiffProps) {
  const { checkpoint } = p.pick;
  const count = p.selected.size;
  return (
    <div className="ws-diff" data-testid="since-last-diff">
      <p className="ws-diff-note" data-testid="since-last-source">
        {iterationSourceNote(p.pick)} <code>{checkpoint.sha.slice(0, 10)}</code> · {checkpoint.message} ·{" "}
        {formatRelativeTime(checkpoint.createdAt)}
      </p>

      {p.notice && (
        <p className="ws-diff-note" role={p.notice.tone === "danger" ? "alert" : "status"}>
          {p.notice.text}
        </p>
      )}

      {p.error ? (
        <ErrorState title="The last iteration didn’t load" message={p.error} onRetry={p.onRetry} />
      ) : p.files.length === 0 ? (
        <EmptyState
          title="No textual changes in the last iteration"
          body="The newest snapshot records no file content changes. Switch to the full pull request to see everything."
        />
      ) : (
        <>
          <p className="ws-diff-note" role="status" data-testid="since-last-summary">
            {summaryLine(p.files)}
          </p>
          {p.files.map((file) => (
            <section key={file.path} className="ws-diff-file" aria-label={`Last iteration of ${file.path}`}>
              <h3 className="ws-diff-path">
                <code>{file.path}</code>
                {file.binary ? <span className="ws-diff-stat">binary</span> : null}
              </h3>
              {file.binary && (
                <p className="ws-diff-note">Binary content. Hunk-level review is not available for this file.</p>
              )}
              {file.hunks.map((hunk) => {
                const key = hunkKey(file.path, hunk.index);
                return (
                  <div key={key} className="ws-hunk" role="group" aria-label={`Hunk ${hunk.index + 1} in ${file.path}`}>
                    <div className="ws-hunk-head">
                      <code>{hunkLabel(hunk)}</code>
                      <label className="ws-diff-reviewed">
                        <input
                          type="checkbox"
                          checked={p.selected.has(key)}
                          onChange={() => p.onToggle(key)}
                          aria-label={`Revert hunk ${hunk.index + 1} in ${file.path}`}
                        />
                        Revert this hunk
                      </label>
                    </div>
                    <div className={`ws-diff-code${p.compact ? " compact" : ""}`} tabIndex={0}
                      aria-label={`Hunk ${hunk.index + 1} in ${file.path}, scrollable horizontally`}>
                      {hunk.lines.map((line, i) => (
                        <div key={i} className={`ws-diff-line ${lineTone(line)}`}>
                          <code>{line || " "}</code>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </section>
          ))}
          <div className="ws-decide-actions">
            <Button variant="danger" size="sm" onClick={p.onRevert} disabled={count === 0 || p.busy}
              loading={p.busy} loadingText="Reverting…" testId="since-last-revert">
              {`Revert ${count} selected hunk${count === 1 ? "" : "s"}`}
            </Button>
          </div>
        </>
      )}

      <p className="ws-diff-note">
        Comments and per-file review ticks are on the full pull request view.
      </p>
    </div>
  );
}
