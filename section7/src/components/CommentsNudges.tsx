"use client";

import { useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too — same as meeting/page.
import * as React from "react";
import { panelStyle, headingStyle, inputStyle, secondaryButtonStyle } from "@/lib/styles";

interface Comment {
  id: string;
  author: string;
  body: string;
  kind: "comment" | "nudge";
  ts: string;
}

export function CommentsNudges({ authorName }: { authorName: string }) {
  const [comments, setComments] = useState<Comment[]>([]);
  const [draft, setDraft] = useState("");

  function post(kind: Comment["kind"]) {
    if (!draft.trim()) return;
    setComments((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        author: authorName || "you",
        body: draft.trim(),
        kind,
        ts: new Date().toISOString(),
      },
    ]);
    setDraft("");
  }

  return (
    <section style={panelStyle}>
      <h2 style={headingStyle}>Run notes</h2>
      {/* M-A1: this used to be labelled "Comments & nudges" and promised the
          agent would read it. It has no backend; the real channel is "Give
          direction" on the Team tab, so the panel now says what it is. */}
      <p style={{ fontSize: 12, color: "var(--muted)", marginTop: -6, marginBottom: 10 }}>
        Private to this browser tab — notes are not sent to the agent. To steer
        the run, use <a href="#direction">Give direction</a> on the Team tab.
      </p>
      <div style={{ display: "grid", gap: 6, maxHeight: 160, overflowY: "auto", marginBottom: 10 }}>
        {comments.map((c) => (
          <div key={c.id} style={{ fontSize: 13 }}>
            <span style={{ color: c.kind === "nudge" ? "var(--yellow)" : "var(--muted)", fontWeight: 600 }}>
              {c.kind === "nudge" ? "↳ to send later" : c.author}:
            </span>{" "}
            {c.body}
          </div>
        ))}
        {comments.length === 0 && (
          <p style={{ color: "var(--muted)", fontSize: 13 }}>
            No notes yet. Write down what you want changed and send it as direction when you are ready.
          </p>
        )}
      </div>
      {/* M15-S3 (W4-VT2 carryover): placeholder-only naming fails WCAG
          4.1.2/3.3.2 — aria-label carries the accessible name; the panel
          heading alone doesn't label this textarea. */}
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={2}
        placeholder="Note something to raise with the agent…"
        aria-label="Note for yourself about this run"
        style={{ ...inputStyle, resize: "vertical" }}
      />
      <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
        <button style={secondaryButtonStyle} onClick={() => post("comment")}>
          Add note
        </button>
        <button style={secondaryButtonStyle} onClick={() => post("nudge")}>
          Flag to send later
        </button>
      </div>
    </section>
  );
}
