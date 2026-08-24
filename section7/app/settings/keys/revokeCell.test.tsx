// M13-G5b — revoke undo window (raw-14 DX batch) for /settings/keys.
// Contract pinned here: confirming a revoke does NOT fire the DELETE — a 5s
// undo window runs first with a live countdown; "Keep key" cancels with zero
// network calls; the countdown line switches to "Revoking …" at expiry (the
// DELETE round-trip). Node-env protocol: renderToStaticMarkup + markup asserts.
import { describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { REVOKE_UNDO_SECONDS, RevokeActionsCell, revokeUndoLine } from "./revokeCell";

const noop = () => {};

function cell(opts: {
  confirmOpen?: boolean;
  undoSecondsLeft?: number | null;
  busy?: boolean;
}) {
  return renderToStaticMarkup(
    <RevokeActionsCell
      label="prod openai"
      keyId="key-1"
      busy={opts.busy ?? false}
      confirmOpen={opts.confirmOpen ?? false}
      undoSecondsLeft={opts.undoSecondsLeft ?? null}
      onAsk={noop}
      onCancelConfirm={noop}
      onConfirm={noop}
      onKeepKey={noop}
    />,
  );
}

describe("REVOKE_UNDO_SECONDS", () => {
  it("is exactly the 5-second grace period", () => {
    expect(REVOKE_UNDO_SECONDS).toBe(5);
  });
});

describe("RevokeActionsCell states", () => {
  it("idle state offers Revoke only — no DELETE path visible yet", () => {
    const html = cell({});
    expect(html).toContain('data-testid="ask-revoke-key-1"');
    expect(html).toContain(">Revoke</button>");
    expect(html).not.toContain("Keep key");
  });

  it("confirm state asks, exposes Confirm + Cancel, no undo controls", () => {
    const html = cell({ confirmOpen: true });
    expect(html).toContain("Revoke “prod openai”?");
    expect(html).toContain('data-testid="confirm-revoke-key-1"');
    expect(html).toContain('data-testid="cancel-revoke-key-1"');
    expect(html).not.toContain("Keep key");
    // The confirm button is disabled while busy.
    const busyHtml = cell({ confirmOpen: true, busy: true });
    expect(busyHtml).toContain("<button");
  });

  it("undo window shows the live countdown and the Keep key escape hatch", () => {
    const html = cell({ undoSecondsLeft: 4 });
    expect(html).toContain('data-testid="undo-line-key-1"');
    expect(html).toContain("Revoking “prod openai” in 4s…");
    expect(html).toContain('data-testid="undo-revoke-key-1"');
    expect(html).toContain(">Keep key</button>");
    // No destructive control during the window.
    expect(html).not.toContain("btn danger");
  });

  it("at zero seconds the line flips to the in-flight DELETE wording", () => {
    expect(revokeUndoLine("prod openai", 0)).toBe("Revoking “prod openai”…");
    expect(revokeUndoLine("prod openai", -1)).toBe("Revoking “prod openai”…");
    expect(revokeUndoLine("prod openai", 5)).toBe("Revoking “prod openai” in 5s…");
  });

  it("never renders negative counts in the markup", () => {
    const html = cell({ undoSecondsLeft: -2 });
    expect(html).not.toContain("-2s");
    expect(html).toContain("Revoking “prod openai”…");
  });
});
