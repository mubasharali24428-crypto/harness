"use client";

// R4: the scope switch above the Diff pane. It finds the run's last iteration
// (newest checkpoint), opens on "Since last iteration" when there is one, and
// switches to the full pull request diff (its children) on request. The
// chosen scope is reported up so the page can hide the snapshot history while
// since-last is primary. Mount it with key={runKey}: a new run starts again
// from the default.

import * as React from "react";
import {
  listCheckpointHunks,
  listCheckpoints,
  revertCheckpointHunks,
  type CheckpointFile,
} from "@/lib/api";
import { selectionsFromKeys, toggleKey } from "@/lib/hunkReview";
import { loadErrorMessage } from "@/lib/humanize";
import {
  isNoHistoryError,
  pickLastIteration,
  visibleIterationFiles,
  type IterationPick,
} from "@/lib/sinceLastIteration";
import { DIFF_SCOPES, defaultDiffScope, type DiffScope } from "@/lib/diffScope";
import { LoadingState } from "@/components/States";
import { SinceLastIterationDiff } from "./SinceLastIterationDiff";

type Phase =
  | { kind: "loading" }
  | { kind: "none" } // no checkpoints (or no project): only the full PR exists
  | { kind: "listError"; message: string }
  | { kind: "ready"; pick: IterationPick };

export function IterationDiffScope({
  projectId,
  runId,
  token,
  compact,
  onDiffScopeChange,
  onIterationFiles,
  children,
}: {
  projectId: string | null;
  runId: string | null;
  token: string;
  compact?: boolean;
  onDiffScopeChange?: (scope: DiffScope | null) => void;
  /**
   * Reports the files the since-last view shows (null until the last
   * iteration has loaded, or when there is none), so the blind-approval KPI
   * can tell whether since-last covered the whole pull request.
   */
  onIterationFiles?: (files: CheckpointFile[] | null) => void;
  /** The full pull request view (DiffPane). */
  children: React.ReactNode;
}) {
  const [phase, setPhase] = React.useState<Phase>({ kind: "loading" });
  const [scope, setScope] = React.useState<DiffScope | null>(null);
  const [files, setFiles] = React.useState<CheckpointFile[]>([]);
  const [hunkError, setHunkError] = React.useState<string | null>(null);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [busy, setBusy] = React.useState(false);
  const [notice, setNotice] = React.useState<{ tone: "success" | "danger"; text: string } | null>(null);

  const report = React.useRef(onDiffScopeChange);
  report.current = onDiffScopeChange;
  React.useEffect(() => {
    report.current?.(scope);
  }, [scope]);
  React.useEffect(() => () => report.current?.(null), []);

  const reportFiles = React.useRef(onIterationFiles);
  reportFiles.current = onIterationFiles;
  const shownFiles = phase.kind === "ready" && hunkError === null ? files : null;
  React.useEffect(() => {
    reportFiles.current?.(shownFiles);
  }, [shownFiles]);
  React.useEffect(() => () => reportFiles.current?.(null), []);

  const loadHunks = React.useCallback(
    async (sha: string) => {
      if (!projectId) return;
      setHunkError(null);
      try {
        const review = await listCheckpointHunks(projectId, sha, token);
        setFiles(visibleIterationFiles(review.files));
        setSelected(new Set());
      } catch (e) {
        setFiles([]);
        setHunkError(loadErrorMessage(e));
      }
    },
    [projectId, token],
  );

  /** Find the last iteration; `keepScope` is false only on first load. */
  const load = React.useCallback(
    async (keepScope: boolean) => {
      if (!projectId) {
        setPhase({ kind: "none" });
        setScope("full-pr");
        return;
      }
      let pick: IterationPick | null = null;
      try {
        const list = await listCheckpoints(projectId, token);
        pick = pickLastIteration(list.items, runId);
      } catch (e) {
        if (!isNoHistoryError(e)) {
          setPhase({ kind: "listError", message: loadErrorMessage(e) });
          if (!keepScope) setScope("full-pr");
          return;
        }
      }
      if (!pick) {
        setPhase({ kind: "none" });
        setScope("full-pr");
        return;
      }
      await loadHunks(pick.checkpoint.sha);
      setPhase({ kind: "ready", pick });
      if (!keepScope) setScope(defaultDiffScope(true));
    },
    [projectId, runId, token, loadHunks],
  );

  React.useEffect(() => {
    void load(false);
    // first load per mount; the parent remounts on a new run (key={runKey})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function revert() {
    if (phase.kind !== "ready" || !projectId || selected.size === 0) return;
    setBusy(true);
    setNotice(null);
    try {
      const out = await revertCheckpointHunks(projectId, phase.pick.checkpoint.sha, selectionsFromKeys(selected), token);
      setNotice({ tone: "success", text: `Reverted ${out.reverted} hunk(s). The revert is now the newest snapshot, ${out.sha.slice(0, 10)}.` });
      await load(true);
    } catch (e) {
      setNotice({ tone: "danger", text: loadErrorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  if (phase.kind === "loading" || scope === null) {
    return <LoadingState label="Finding the last iteration…" rows={2} />;
  }

  const sinceLastAvailable = phase.kind === "ready" || phase.kind === "listError";

  return (
    <div className="ws-scope" data-testid="diff-scope" data-scope={scope}>
      <div className="ws-scope-toggle" role="group" aria-label="Diff scope">
        {DIFF_SCOPES.map((s) => (
          <button
            key={s.id}
            type="button"
            className="ws-pane-tab"
            aria-pressed={scope === s.id}
            disabled={s.id === "since-last" && !sinceLastAvailable}
            data-testid={`diff-scope-${s.id}`}
            onClick={() => setScope(s.id)}
          >
            {s.label}
          </button>
        ))}
      </div>
      {phase.kind === "none" && (
        <p className="ws-diff-note" data-testid="since-last-none">
          No iteration snapshots are recorded for this project yet, so this is the full pull request.
        </p>
      )}

      {scope === "since-last" && phase.kind === "ready" ? (
        <SinceLastIterationDiff
          pick={phase.pick}
          files={files}
          error={hunkError}
          selected={selected}
          busy={busy}
          notice={notice}
          compact={compact}
          onToggle={(key) => setSelected((cur) => toggleKey(cur, key))}
          onRevert={() => void revert()}
          onRetry={() => void loadHunks(phase.pick.checkpoint.sha)}
        />
      ) : scope === "since-last" && phase.kind === "listError" ? (
        <div role="alert" className="ws-diff-note">
          Snapshots didn’t load: {phase.message}{" "}
          <button type="button" className="ws-pane-tab" onClick={() => void load(true)}>Try again</button>
        </div>
      ) : (
        children
      )}
    </div>
  );
}
