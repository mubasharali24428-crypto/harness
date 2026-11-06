import * as React from "react";
import { panelStyle, headingStyle, buttonStyle, secondaryButtonStyle, inputStyle } from "@/lib/styles";

/** Hand focus back to the control that opened the editor (the "Edit scope"
 *  button) when the editor closes. Pure DOM, so a node-env test can drive it
 *  with a hand-built element fake. */
export function restoreTriggerFocus(el: HTMLElement | null | undefined): void {
  el?.focus();
}

/** Deliberately hook-free and form-free: a node-env test can call this as a
 *  plain function and walk the returned element tree to prove that ONLY the
 *  "Save scope" button reaches onConfirm. Nothing here submits on its own. */
export function ScopeRequestEditor({
  pathsText,
  onPathsTextChange,
  onConfirm,
  onCancel,
  busy = false,
  error = null,
}: {
  pathsText: string;
  onPathsTextChange: (next: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
  error?: string | null;
}) {
  return (
    <section style={panelStyle} aria-label="Edit scope"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          onCancel();
        }
      }}>
      <h2 style={headingStyle}>Edit scope</h2>
      <p id="scope-request-warning" style={{ color: "var(--muted)", fontSize: 13 }}>
        Saving this scope edit voids the run&apos;s approvals and asks for
        approval again. The request grants nothing by itself.
      </p>
      <label htmlFor="scope-request-paths" style={{ fontSize: 12 }}>
        Scope paths (one per line)
      </label>
      <textarea
        id="scope-request-paths"
        data-testid="scope-paths"
        value={pathsText}
        onChange={(e) => onPathsTextChange(e.target.value)}
        disabled={busy}
        autoFocus
        rows={4}
        style={inputStyle}
      />
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button
          type="button"
          data-testid="scope-save"
          disabled={busy}
          aria-describedby="scope-request-warning"
          style={buttonStyle}
          onClick={onConfirm}
        >
          Save scope
        </button>
        <button
          type="button"
          data-testid="scope-cancel"
          disabled={busy}
          style={secondaryButtonStyle}
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
      {error !== null && <p role="alert" style={{ color: "var(--red)", fontSize: 13 }}>{error}</p>}
    </section>
  );
}
