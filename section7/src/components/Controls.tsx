"use client";

import * as React from "react";
import { useState } from "react";
import type { RunStatus } from "@contracts/api";
import { pauseAgent, resumeAgent, resumeWithChanges, stopAgent, submitHumanEdit, signalDone, ApiError } from "@/lib/api";
import { useEditorView } from "@/lib/codemirror";
import {
  panelStyle,
  headingStyle,
  buttonStyle,
  secondaryButtonStyle,
  dangerButtonStyle,
  inputStyle,
} from "@/lib/styles";

export function Controls({
  runId,
  stagedPending = false,
  token,
  status,
  onDiffChange,
  onActionComplete,
}: {
  runId: string;
  stagedPending?: boolean;
  token: string;
  status: RunStatus | null;
  onDiffChange: (diff: string) => void;
  onActionComplete: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [takeover, setTakeover] = useState(false);
  const [editPath, setEditPath] = useState("");
  const [editContent, setEditContent] = useState("");
  const [change, setChange] = useState("");
  const editorRef = useEditorView(editContent, { onChange: setEditContent, label: "New file content" });

  async function run(action: string, fn: () => Promise<unknown>) {
    setBusy(action);
    setError(null);
    try {
      await fn();
      onActionComplete();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed");
    } finally {
      setBusy(null);
    }
  }

  // M-A6: this used to POST the human edit the instant the box was ticked,
  // against a placeholder path and whatever the editor held — so merely peeking
  // at the controls paused the run and recorded an empty edit against it. The
  // checkbox now only reveals the editor; the pause + edit happen together on
  // "Save edit", which is the endpoint's submit-shaped semantics.
  function handleTakeoverToggle(checked: boolean) {
    setTakeover(checked);
    if (!checked) setError(null);
  }

  const state = status?.state;
  const canPause = state === "executing";
  const canResume = state === "paused" || state === "blocked";
  const canStop = !!state && state !== "done";

  return (
    <section style={panelStyle}>
      <h2 style={headingStyle}>Controls</h2>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <button
          style={buttonStyle}
          disabled={!canPause || busy !== null}
          onClick={() => run("pause", () => pauseAgent(runId, token))}
        >
          Pause
        </button>
        <button
          style={secondaryButtonStyle}
          disabled={!canResume || busy !== null}
          onClick={() => run("resume", () => resumeAgent(runId, token))}
        >
          Resume
        </button>
        <button
          style={dangerButtonStyle}
          disabled={!canStop || busy !== null}
          onClick={() => run("stop", () => stopAgent(runId, token))}
        >
          Stop
        </button>
        <button
          style={secondaryButtonStyle}
          disabled={busy !== null || stagedPending || !state || state === "done"}
          onClick={() =>
            run("done", async () => {
              await signalDone(runId, editContent, token);
              onDiffChange(editContent);
            })
          }
        >
          Done, continue
        </button>
      </div>

      {/* W2 (N04): the visible retry. A stuck run is resumed *with a change* —
          the text is recorded as durable guidance before the resume call, so the
          agent reads it on the next attempt. Disabled with nothing typed,
          because resuming without a change is what the Resume button already
          does and a second button for it would be decoration. */}
      {canResume && (
        <div
          style={{
            marginBottom: 12,
            paddingTop: 12,
            borderTop: "1px solid var(--border-soft)",
          }}
        >
          <label
            htmlFor="resume-change"
            style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 4 }}
          >
            Resume with changes
          </label>
          <p style={{ color: "var(--muted)", fontSize: 12, margin: "0 0 8px" }}>
            Saved as guidance the agent reads before the agent continues — not a chat
            message, and not a restart.
          </p>
          <input
            id="resume-change"
            value={change}
            onChange={(e) => setChange(e.target.value)}
            placeholder="e.g. use the existing helper instead of a new module"
            aria-label="What should change before resuming"
            style={inputStyle}
          />
          <button
            style={{ ...secondaryButtonStyle, marginTop: 8 }}
            disabled={busy !== null || !change.trim()}
            onClick={() =>
              run("resume-with-changes", async () => {
                await resumeWithChanges(runId, change, token);
                setChange("");
              })
            }
          >
            Resume with changes
          </button>
        </div>
      )}

      {stagedPending && <p role="status">Resolve staged changes before completing this run.</p>}
      {error && <p style={{ color: "var(--red)", fontSize: 13 }}>{error}</p>}

      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, marginBottom: 8 }}>
        <input
          type="checkbox"
          checked={takeover}
          onChange={(e) => handleTakeoverToggle(e.target.checked)}
        />
        Edit a file myself
      </label>
      {takeover && (
        <p style={{ color: "var(--muted)", fontSize: 12, margin: "0 0 8px" }}>
          Saving pauses the run so the agent can't overwrite your change.
        </p>
      )}

      {takeover && (
        <div>
          {/* M15-S3: placeholder-only naming fails WCAG 4.1.2/3.3.2 —
              aria-label names the field; the checkbox label above doesn't. */}
          <input
            value={editPath}
            onChange={(e) => setEditPath(e.target.value)}
            placeholder="file path"
            aria-label="File path to edit"
            style={inputStyle}
          />
          <div
            ref={editorRef}
            style={{
              height: 160,
              marginTop: 8,
              border: "1px solid var(--border-soft)",
              borderRadius: 6,
              overflow: "hidden",
            }}
          />
          <button
            style={{ ...buttonStyle, marginTop: 8 }}
            disabled={busy !== null || !editPath}
            onClick={() =>
              run("human-edit", async () => {
                await submitHumanEdit(runId, editPath, editContent, token);
                onDiffChange(editContent);
              })
            }
          >
            Save edit
          </button>
        </div>
      )}
    </section>
  );
}
