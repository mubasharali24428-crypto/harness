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
      <h2 style={headingStyle}>Comments &amp; nudges</h2>
      <p style={{ fontSize: 12, color: "#6b7280", marginTop: -6, marginBottom: 10 }}>
        Local to this browser session — no backend endpoint exists yet to persist or
        deliver these to the agent.
      </p>
      <div style={{ display: "grid", gap: 6, maxHeight: 160, overflowY: "auto", marginBottom: 10 }}>
        {comments.map((c) => (
          <div key={c.id} style={{ fontSize: 13 }}>
            <span style={{ color: c.kind === "nudge" ? "#fbbf24" : "#9aa4b2", fontWeight: 600 }}>
              {c.kind === "nudge" ? "↳ nudge" : c.author}:
            </span>{" "}
            {c.body}
          </div>
        ))}
        {comments.length === 0 && <p style={{ color: "#6b7280", fontSize: 13 }}>No comments yet.</p>}
      </div>
      {/* M15-S3 (W4-VT2 carryover): placeholder-only naming fails WCAG
          4.1.2/3.3.2 — aria-label carries the accessible name; the panel
          heading alone doesn't label this textarea. */}
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={2}
        placeholder="Leave a comment or redirect for the agent…"
        aria-label="Leave a comment or nudge for the agent"
        style={{ ...inputStyle, resize: "vertical" }}
      />
      <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
        <button style={secondaryButtonStyle} onClick={() => post("comment")}>
          Comment
        </button>
        <button style={secondaryButtonStyle} onClick={() => post("nudge")}>
          Nudge / redirect
        </button>
      </div>
    </section>
  );
}
