"use client";

// M13-G5b (raw-14) — revoke undo-window cell for /settings/keys.
// Lives next to the page (page.tsx must keep Next-only exports). Three states:
// idle Revoke → inline confirm → 5s undo window with a live countdown.
// The DELETE only fires when the countdown reaches zero; "Keep key" cancels
// with ZERO network calls made. Pure-presentational (same protocol as
// RunsTable/MembersTable), so vitest renders it without Next routing.

// Classic JSX transform (vitest/esbuild) needs React in scope for .tsx.
import * as React from "react";

/** Seconds between confirming a revoke and the DELETE actually firing. */
export const REVOKE_UNDO_SECONDS = 5;

/** Pure: status line during the undo window (secondsLeft > 0) and during the
 *  final DELETE round-trip (secondsLeft <= 0). */
export function revokeUndoLine(label: string, secondsLeft: number): string {
  return secondsLeft > 0
    ? `Revoking “${label}” in ${secondsLeft}s…`
    : `Revoking “${label}”…`;
}

/** Presentational actions cell for one active key row. */
export function RevokeActionsCell({
  label,
  keyId,
  busy,
  confirmOpen,
  undoSecondsLeft,
  onAsk,
  onCancelConfirm,
  onConfirm,
  onKeepKey,
}: {
  label: string;
  keyId: string;
  busy: boolean;
  confirmOpen: boolean;
  /** Non-null while this key's undo window is running (counts down to 0). */
  undoSecondsLeft: number | null;
  onAsk: () => void;
  onCancelConfirm: () => void;
  onConfirm: () => void;
  onKeepKey: () => void;
}) {
  if (undoSecondsLeft !== null) {
    return (
      <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
        <span style={{ color: "var(--yellow)" }} data-testid={`undo-line-${keyId}`}>
          {revokeUndoLine(label, Math.max(0, undoSecondsLeft))}
        </span>
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={onKeepKey}
          data-testid={`undo-revoke-${keyId}`}
          aria-label={`Cancel revoking key ${label}`}
        >
          Keep key
        </button>
      </span>
    );
  }
  if (confirmOpen) {
    return (
      <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
        Revoke “{label}”?
        <button
          type="button"
          className="btn danger"
          disabled={busy}
          onClick={onConfirm}
          data-testid={`confirm-revoke-${keyId}`}
          aria-label={`Confirm revoking key ${label}`}
        >
          Confirm
        </button>
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={onCancelConfirm}
          data-testid={`cancel-revoke-${keyId}`}
        >
          Cancel
        </button>
      </span>
    );
  }
  return (
    <button
      type="button"
      className="btn danger"
      disabled={busy}
      onClick={onAsk}
      data-testid={`ask-revoke-${keyId}`}
      aria-label={`Revoke key ${label}`}
    >
      Revoke
    </button>
  );
}
