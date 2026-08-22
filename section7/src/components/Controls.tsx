"use client";

import { useState } from "react";
import type { RunStatus } from "@contracts/api";
import { pauseAgent, resumeAgent, stopAgent, submitHumanEdit, signalDone, ApiError } from "@/lib/api";
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
  token,
  status,
  onDiffChange,
  onActionComplete,
}: {
  runId: string;
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
  const editorRef = useEditorView(editContent, { onChange: setEditContent });

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

  // ponytail: the backend only exposes a submit-shaped human-edit endpoint (no
  // separate "started editing" signal), so checking the box IS the implicit-pause
  // trigger - the closest honest match to "pause the moment a human starts editing"
  // without inventing a keystroke-level API that doesn't exist server-side.
  function handleTakeoverToggle(checked: boolean) {
    setTakeover(checked);
    if (checked) {
      run("human-edit", () => submitHumanEdit(runId, editPath || "(unspecified)", editContent, token));
    }
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
          disabled={busy !== null}
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

      {error && <p style={{ color: "#f87171", fontSize: 13 }}>{error}</p>}

      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, marginBottom: 8 }}>
        <input
          type="checkbox"
          checked={takeover}
          onChange={(e) => handleTakeoverToggle(e.target.checked)}
        />
        Human takeover / manual edit
      </label>

      {takeover && (
        <div>
          <input
            value={editPath}
            onChange={(e) => setEditPath(e.target.value)}
            placeholder="file path"
            style={inputStyle}
          />
          <div
            ref={editorRef}
            style={{
              height: 160,
              marginTop: 8,
              border: "1px solid #2a3038",
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
