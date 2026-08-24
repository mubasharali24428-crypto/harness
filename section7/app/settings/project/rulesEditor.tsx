"use client";

// M14-W4 gate repair — presentational rules editor extracted from
// app/settings/project/page.tsx (M12-G10) so the page module keeps ONLY
// Next's allowed route exports. Named export lets component tests render it
// without Next routing or a live session.
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too, so one source fits both.
import { useState } from "react";
import * as React from "react";
import { MAX_RULES_BYTES, ProjectRulesDto, rulesByteLength } from "./projectRulesApi";

const cardStyle = { maxWidth: 760 } as const;
const textareaStyle = {
  width: "100%",
  minHeight: 220,
  background: "var(--panel-2)",
  border: "1px solid var(--border)",
  color: "var(--text)",
  borderRadius: 6,
  padding: "8px 10px",
  font: "13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace",
  resize: "vertical",
} as const;

/** Pure presentational panel — exported for tests, SSR-safe. */
export function RulesEditor({
  projectId,
  initial,
  canEdit,
  busy,
  onTextChange,
  onSave,
  onClear,
}: {
  projectId: string;
  initial: ProjectRulesDto | null;
  canEdit: boolean;
  /** True while any save/clear round-trip is in flight. */
  busy: boolean;
  onTextChange: (text: string) => void;
  onSave: () => void;
  onClear: () => void;
}) {
  const [text, setText] = useState(initial?.rulesText ?? "");
  const chars = text.length;
  const bytes = rulesByteLength(text);
  const overCap = bytes > MAX_RULES_BYTES;

  return (
    <section className="card" style={cardStyle}>
      <h2>Project rules</h2>
      <p style={{ color: "var(--muted)", fontSize: 12 }}>
        Conventions every agent plan must honor — like <code>.cursorrules</code>{" "}
        for this project. Advisory: agents are guided by these rules, but hard
        limits (sandbox allow-list, forbidden paths) still apply regardless.
      </p>
      {!canEdit && (
        <p style={{ color: "var(--muted)", fontSize: 12 }}>
          Editing is restricted to team admins and engineers.
        </p>
      )}
      <textarea
        data-testid="rules-textarea"
        aria-label="Project rules"
        value={text}
        readOnly={!canEdit}
        disabled={busy && canEdit}
        onChange={(e) => {
          setText(e.target.value);
          onTextChange(e.target.value);
        }}
        placeholder={"e.g.\nAlways use pnpm, never npm.\nNo default exports outside app/."}
        style={{ ...textareaStyle, opacity: canEdit ? 1 : 0.75 }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8 }}>
        {/* Char + byte counter; turns red over the server cap */}
        <span
          data-testid="rules-counter"
          role="status"
          style={{
            fontSize: 12,
            color:
              bytes > MAX_RULES_BYTES
                ? "var(--red)"
                : chars > MAX_RULES_BYTES * 0.9
                  ? "var(--yellow)"
                  : "var(--muted)",
          }}
        >
          {chars.toLocaleString()} chars · {bytes.toLocaleString()} /{" "}
          {(MAX_RULES_BYTES / 1024).toFixed(0)} KB
        </span>
        {overCap && (
          <span role="alert" style={{ fontSize: 12, color: "var(--red)" }}>
            Over the {MAX_RULES_BYTES / 1024} KB limit.
          </span>
        )}
        {canEdit && (
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8 }}>
            <button
              type="button"
              className="btn danger"
              data-testid="rules-clear"
              disabled={busy || (!initial?.rulesText && !text)}
              onClick={() => {
                setText("");
                onClear();
              }}
            >
              Clear
            </button>
            <button
              type="button"
              className="btn primary"
              data-testid="rules-save"
              disabled={busy || overCap}
              onClick={() => onSave()}
            >
              {busy ? "Saving…" : "Save rules"}
            </button>
          </span>
        )}
      </div>
      {initial?.updatedAt && (
        <p style={{ color: "var(--muted)", fontSize: 11, marginTop: 6 }}>
          Last updated {new Date(initial.updatedAt).toLocaleString()}
          {initial.updatedBy ? ` · by ${initial.updatedBy}` : ""}
        </p>
      )}
    </section>
  );
}
