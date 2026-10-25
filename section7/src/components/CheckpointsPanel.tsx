"use client";

// M12-G9 (first slice): minimal checkpoint browser. Lists git snapshots the
// sandbox took around each agent edit batch, newest first; "view diff" opens
// the raw unified diff for one snapshot and renders it as plain text — no
// syntax highlighting this slice.

import { useCallback, useEffect, useState } from "react";
import {
  listCheckpoints,
  getCheckpointDiff,
  type CheckpointDto,
} from "@/lib/api";
import { panelStyle, headingStyle } from "@/lib/styles";

export function CheckpointsPanel({
  projectId,
  token,
  refreshKey = 0,
}: {
  projectId: string;
  token: string;
  /** Bump (e.g. with the live event count) to re-pull the snapshot list. */
  refreshKey?: number;
}) {
  const [items, setItems] = useState<CheckpointDto[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openSha, setOpenSha] = useState<string | null>(null);
  const [diffText, setDiffText] = useState("");
  const [diffLoading, setDiffLoading] = useState(false);

  const refresh = useCallback(() => {
    listCheckpoints(projectId, token)
      .then((list) => {
        setItems(list.items);
        setError(null);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      });
  }, [projectId, token]);

  useEffect(() => {
    refresh();
  }, [refresh, refreshKey]);

  async function toggleDiff(sha: string) {
    if (openSha === sha) {
      setOpenSha(null);
      setDiffText("");
      return;
    }
    setOpenSha(sha);
    setDiffLoading(true);
    try {
      const text = await getCheckpointDiff(projectId, sha, token);
      setDiffText(text);
    } catch (err) {
      setDiffText(err instanceof Error ? err.message : String(err));
    } finally {
      setDiffLoading(false);
    }
  }

  return (
    <section style={panelStyle}>
      <h2 style={headingStyle}>Checkpoints</h2>
      {error && <p style={{ color: "var(--red)", fontSize: 13 }}>{error}</p>}
      {!error && items === null && (
        <p style={{ color: "var(--muted)", fontSize: 13 }}>Loading snapshots…</p>
      )}
      {items !== null && items.length === 0 && (
        <p style={{ color: "var(--muted)", fontSize: 13 }}>
          No snapshots yet. One pair is recorded per agent edit batch.
        </p>
      )}
      {items !== null && items.length > 0 && (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {items.map((cp) => (
            <li key={cp.sha} style={{ padding: "6px 0", borderBottom: "1px solid #232936" }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                <code style={{ color: "#93c5fd", fontSize: 12 }}>{cp.sha.slice(0, 7)}</code>
                <span style={{ fontSize: 13, flex: 1 }}>{cp.message}</span>
                <button
                  type="button"
                  onClick={() => void toggleDiff(cp.sha)}
                  style={{
                    fontSize: 12,
                    background: "none",
                    border: "1px solid var(--border-soft)",
                    borderRadius: 4,
                    color: openSha === cp.sha ? "var(--yellow)" : "var(--muted)",
                    cursor: "pointer",
                    padding: "2px 8px",
                  }}
                >
                  {openSha === cp.sha ? "hide diff" : "view diff"}
                </button>
              </div>
              <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
                task {cp.taskId} · {new Date(cp.createdAt).toLocaleString()}
              </div>
              {openSha === cp.sha && (
                <pre
                  data-testid="checkpoint-diff"
                  style={{
                    marginTop: 6,
                    padding: 8,
                    background: "var(--bg)",
                    border: "1px solid var(--border-soft)",
                    borderRadius: 6,
                    fontSize: 12,
                    overflowX: "auto",
                    whiteSpace: "pre-wrap",
                    maxHeight: 260,
                  }}
                >
                  {diffLoading ? "Loading diff…" : diffText || "(empty diff)"}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
