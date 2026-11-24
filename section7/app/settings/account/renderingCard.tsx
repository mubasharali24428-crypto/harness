"use client";

// Task 13: X-D1 self-service preference — a small, display-only control. The three
// labels are the X-D1 renderings; nothing here changes permissions or the risk tier.
import * as React from "react";
import { RENDERINGS, type Rendering } from "./renderingApi";

const LABELS: Record<Rendering, string> = {
  technical: "Technical",
  plain: "Plain",
  decision: "Decision card",
};

export function RenderingCard(props: {
  value: Rendering;
  onChange: (r: Rendering) => void;
  onSave: () => void;
  busy?: boolean;
  notice?: string | null;
  error?: string | null;
}) {
  const { value, onChange, onSave, busy, notice, error } = props;
  return (
    <section className="card" aria-labelledby="rendering-heading">
      <h2 id="rendering-heading">Approval rendering</h2>
      <p id="rendering-help">Changes how approvals are shown; does not change permissions.</p>
      <fieldset aria-describedby="rendering-help">
        <legend>How approvals look to you</legend>
        {RENDERINGS.map((r) => (
          <label key={r}>
            <input
              type="radio"
              name="approval-rendering"
              value={r}
              checked={value === r}
              onChange={() => onChange(r)}
            />{" "}
            {LABELS[r]}
          </label>
        ))}
      </fieldset>
      <button type="button" className="btn" disabled={busy} onClick={onSave}>
        {busy ? "Saving…" : "Save rendering"}
      </button>
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
