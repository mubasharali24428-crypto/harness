"use client";

// C16: visual review of a checkpoint's changes, hunk by hunk.
//
// The server parses each snapshot's own diff and returns hunks; this view
// only marks the hunks the reviewer wants to REVERT. Keeping is the default,
// so an untouched screen never changes anything. Restore is a separate,
// confirmed action that resets the workspace tree to the snapshot.
//
// SSR-safe presentational split (ChangeReviewView) mirrors the rest of the
// repo: the node-environment tests render markup without a DOM.

import * as React from "react";
import {
  ApiError,
  listCheckpoints,
  listCheckpointHunks,
  restoreCheckpoint,
  revertCheckpointHunks,
  type CheckpointDto,
  type CheckpointFile,
} from "@/lib/api";
import { hunkKey, hunkLabel, lineTone, selectionsFromKeys, summaryLine, toggleKey } from "@/lib/hunkReview";
import { Button, Modal, Pill } from "./ui";
import { Notice } from "./ui/Notice";
import { EmptyState, ErrorState, LoadingState } from "./States";
import { cssVar, fontSize, radius, space } from "./tokens";

const TONE_COLOR = {
  add: cssVar("green"),
  del: cssVar("red"),
  meta: cssVar("muted"),
  context: cssVar("text"),
} as const;

const monospace: React.CSSProperties = {
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  fontSize: fontSize.sm,
  background: cssVar("panel2"),
  border: `1px solid ${cssVar("border")}`,
  borderRadius: radius.md,
  padding: space.md,
  overflowX: "auto",
  margin: "6px 0 0",
};

export interface ChangeReviewViewProps {
  /** Render nothing when there are no snapshots (the caller already shows the run's changes). */
  quietWhenEmpty?: boolean;
  snapshots: CheckpointDto[];
  sha: string;
  files: CheckpointFile[];
  selected: ReadonlySet<string>;
  busy: "revert" | "restore" | null;
  notice: { tone: "success" | "danger"; text: string } | null;
  error: string | null;
  confirmRestore: boolean;
  onSelectSha: (sha: string) => void;
  onToggle: (key: string) => void;
  onRevert: () => void;
  onRequestRestore: () => void;
  onCancelRestore: () => void;
  onConfirmRestore: () => void;
  onRetry: () => void;
}

export function ChangeReviewView(p: ChangeReviewViewProps) {
  if (p.snapshots.length === 0) {
    if (p.quietWhenEmpty) return null;
    return (
      <section aria-labelledby="changes-heading">
        <h2 id="changes-heading">Changes</h2>
        <EmptyState
          title="No snapshots yet"
          body="Snapshots appear after the agent records its first edit batch. Nothing is hidden — there is simply no change history for this project yet."
        />
      </section>
    );
  }

  const selectedCount = p.selected.size;
  return (
    <section aria-labelledby="changes-heading" data-testid="change-review">
      <h2 id="changes-heading">Changes</h2>
      <p style={{ color: cssVar("muted"), fontSize: fontSize.sm }}>
        Every agent edit batch is snapshotted. Tick a hunk to revert it; restore
        resets the tracked files to a snapshot and records a new checkpoint.
      </p>

      {p.notice && (
        <Notice tone={p.notice.tone} role={p.notice.tone === "danger" ? "alert" : "status"}>
          {p.notice.text}
        </Notice>
      )}

      <label
        htmlFor="snapshot-select"
        style={{ display: "block", margin: "14px 0 4px", fontSize: fontSize.sm }}
      >
        Snapshot
      </label>
      <select
        id="snapshot-select"
        value={p.sha}
        onChange={(e) => p.onSelectSha(e.target.value)}
        style={{ minWidth: 320, maxWidth: "100%" }}
      >
        {p.snapshots.map((s) => (
          <option key={s.sha} value={s.sha}>
            {s.sha.slice(0, 10)} · {s.message}
          </option>
        ))}
      </select>

      {p.error && <ErrorState title="This snapshot didn’t load" message={p.error} onRetry={p.onRetry} />}

      {!p.error && p.files.length === 0 && (
        <EmptyState
          title="No textual changes in this snapshot"
          body="The snapshot exists but records no file content changes (for example a baseline commit)."
        />
      )}

      {!p.error && p.files.length > 0 && (
        <>
          <p style={{ color: cssVar("muted"), fontSize: fontSize.sm, margin: "12px 0 4px" }} data-testid="change-summary">
            {summaryLine(p.files)}
          </p>
          {p.files.map((file) => (
            <article
              key={file.path}
              aria-label={`Changes in ${file.path}`}
              style={{
                border: `1px solid ${cssVar("border")}`,
                borderRadius: radius.lg,
                padding: space.lg,
                margin: "10px 0",
                background: cssVar("panel"),
              }}
            >
              <h3 style={{ margin: 0, fontSize: fontSize.md }}>
                <code>{file.path}</code>{" "}
                {file.binary && <Pill tone="yellow">binary</Pill>}
              </h3>
              {file.binary && (
                <p style={{ color: cssVar("muted"), fontSize: fontSize.sm }}>
                  Binary content — hunk-level review is not available for this file.
                </p>
              )}
              {file.hunks.map((hunk) => {
                const key = hunkKey(file.path, hunk.index);
                const marked = p.selected.has(key);
                return (
                  <div
                    key={key}
                    role="group"
                    aria-label={`Hunk ${hunk.index + 1} in ${file.path}`}
                    style={{ marginTop: 10 }}
                  >
                    <div
                      style={{
                        display: "flex",
                        flexWrap: "wrap",
                        gap: 10,
                        alignItems: "center",
                        justifyContent: "space-between",
                      }}
                    >
                      <code style={{ color: cssVar("accent"), fontSize: fontSize.xs }}>
                        {hunkLabel(hunk)}
                      </code>
                      <label style={{ display: "inline-flex", gap: 6, alignItems: "center", fontSize: fontSize.sm }}>
                        <input
                          type="checkbox"
                          checked={marked}
                          onChange={() => p.onToggle(key)}
                          aria-label={`Revert hunk ${hunk.index + 1} in ${file.path}`}
                        />
                        Revert this hunk
                      </label>
                    </div>
                    <div style={monospace} role="presentation">
                      {hunk.lines.map((line, i) => (
                        <div
                          key={i}
                          data-tone={lineTone(line)}
                          style={{ color: TONE_COLOR[lineTone(line)], whiteSpace: "pre" }}
                        >
                          {line || " "}
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </article>
          ))}

          <div style={{ display: "flex", gap: space.md, flexWrap: "wrap", marginTop: space.xl }}>
            <Button
              variant="danger"
              size="sm"
              onClick={p.onRevert}
              disabled={selectedCount === 0 || p.busy !== null}
              loading={p.busy === "revert"}
              loadingText="Reverting…"
            >
              {`Revert ${selectedCount} selected hunk${selectedCount === 1 ? "" : "s"}`}
            </Button>
            <Button
              size="sm"
              onClick={p.onRequestRestore}
              disabled={p.busy !== null}
              loading={p.busy === "restore"}
              loadingText="Restoring…"
            >
              Restore this snapshot…
            </Button>
          </div>
        </>
      )}

      <Modal
        open={p.confirmRestore}
        onClose={p.onCancelRestore}
        title="Restore this snapshot?"
        testId="restore-confirm"
      >
        <p style={{ fontSize: fontSize.sm, lineHeight: 1.5 }}>
          Tracked files return to the state recorded by{" "}
          <code>{p.sha.slice(0, 12)}</code>, and the restore is itself recorded
          as a new checkpoint. Untracked files are left alone. Continue?
        </p>
        <div style={{ display: "flex", gap: space.md, justifyContent: "flex-end" }}>
          <Button size="sm" onClick={p.onCancelRestore}>
            Cancel
          </Button>
          <Button size="sm" variant="danger" onClick={p.onConfirmRestore}>
            Restore snapshot
          </Button>
        </div>
      </Modal>
    </section>
  );
}

export function ChangeReview({
  projectId,
  token,
  quietWhenEmpty,
}: {
  projectId: string;
  token: string;
  quietWhenEmpty?: boolean;
}) {
  const [snapshots, setSnapshots] = React.useState<CheckpointDto[] | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [sha, setSha] = React.useState("");
  const [files, setFiles] = React.useState<CheckpointFile[]>([]);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [busy, setBusy] = React.useState<"revert" | "restore" | null>(null);
  const [notice, setNotice] = React.useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [hunkError, setHunkError] = React.useState<string | null>(null);
  const [confirmRestore, setConfirmRestore] = React.useState(false);

  const message = (e: unknown) => (e instanceof ApiError ? e.message : String((e as Error)?.message ?? e));

  const loadSnapshots = React.useCallback(
    async (preferred?: string) => {
      setLoadError(null);
      try {
        const list = await listCheckpoints(projectId, token);
        setSnapshots(list.items);
        const next = preferred || sha || list.items[0]?.sha || "";
        setSha(next);
      } catch (e) {
        // A project the sandbox has never snapshotted is an empty history,
        // not a failure: say so instead of surfacing a 404 envelope.
        if (e instanceof ApiError && (e.code === "CHECKPOINT_REPOSITORY_NOT_FOUND" || e.status === 404)) {
          setSnapshots([]);
          setSha("");
          return;
        }
        setLoadError(message(e));
      }
    },
    [projectId, token, sha],
  );

  React.useEffect(() => {
    void loadSnapshots();
    // first load only; reloads happen after a mutation
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, token]);

  const loadHunks = React.useCallback(
    async (target: string) => {
      if (!target) return;
      setHunkError(null);
      try {
        const review = await listCheckpointHunks(projectId, target, token);
        setFiles(review.files);
        setSelected(new Set());
      } catch (e) {
        setFiles([]);
        setHunkError(message(e));
      }
    },
    [projectId, token],
  );

  React.useEffect(() => {
    void loadHunks(sha);
  }, [sha, loadHunks]);

  async function doRevert() {
    setBusy("revert");
    setNotice(null);
    try {
      const out = await revertCheckpointHunks(projectId, sha, selectionsFromKeys(selected), token);
      setNotice({ tone: "success", text: `Reverted ${out.reverted} hunk(s). New checkpoint ${out.sha.slice(0, 10)}.` });
      await loadSnapshots(out.sha);
    } catch (e) {
      setNotice({ tone: "danger", text: message(e) });
    } finally {
      setBusy(null);
    }
  }

  async function doRestore() {
    setBusy("restore");
    setConfirmRestore(false);
    setNotice(null);
    try {
      const out = await restoreCheckpoint(projectId, sha, token);
      setNotice({ tone: "success", text: `Restored to ${out.restoredTo.slice(0, 10)}. New checkpoint ${out.sha.slice(0, 10)}.` });
      await loadSnapshots(out.sha);
    } catch (e) {
      setNotice({ tone: "danger", text: message(e) });
    } finally {
      setBusy(null);
    }
  }

  if (snapshots === null) {
    return loadError ? (
      <ErrorState title="Snapshots didn’t load" message={loadError} onRetry={() => void loadSnapshots()} />
    ) : (
      <LoadingState label="Loading change history…" />
    );
  }

  return (
    <ChangeReviewView
      quietWhenEmpty={quietWhenEmpty}
      snapshots={snapshots}
      sha={sha}
      files={files}
      selected={selected}
      busy={busy}
      notice={notice}
      error={hunkError}
      confirmRestore={confirmRestore}
      onSelectSha={setSha}
      onToggle={(key) => setSelected((current) => toggleKey(current, key))}
      onRevert={() => void doRevert()}
      onRequestRestore={() => setConfirmRestore(true)}
      onCancelRestore={() => setConfirmRestore(false)}
      onConfirmRestore={() => void doRestore()}
      onRetry={() => void loadHunks(sha)}
    />
  );
}
