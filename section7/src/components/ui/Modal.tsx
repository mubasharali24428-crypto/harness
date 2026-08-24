// ui/Modal — DS lane (M14-W4). Focus-trapped dialog over the app's panel
// surface. Built for the confirm-style dialogs the feature lanes need (roster
// removal, invite revocation) and any future detail popovers.
//
// A11y contract (WAI-ARIA dialog pattern):
//   • role="dialog" + aria-modal="true" + accessible name via
//     aria-labelledby → the rendered title element;
//   • Esc closes (keydown on the overlay);
//   • Tab / Shift+Tab cycle INSIDE the dialog only (wrap-around over
//     focusable descendants; focus outside the panel is pulled back in);
//   • initial focus lands on the first focusable control;
//   • backdrop click closes; clicks inside the panel do not;
//   • background page scroll locks while open and restores on unmount.
//
// The Tab-cycle DECISION is a pure exported function (resolveTabTarget) so it
// is unit-testable in the repo's node-environment vitest without a DOM; the
// component only wires that decision to real focus() calls.
//
// SSR-safe: pure inline styles; renders in place (no portal needed for
// renderToStaticMarkup tests).

import * as React from "react";
import { cssVar, radius } from "../tokens";

export interface ModalProps {
  /** Visibility switch. False renders nothing (callers keep state trivial). */
  open: boolean;
  /** Called by Esc and backdrop click. Content controls close themselves. */
  onClose: () => void;
  /** Dialog heading — also becomes the accessible name. */
  title: string;
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  testId?: string;
}

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

/**
 * Pure focus-trap decision table.
 *
 * @param activeIndex index of the focused element within the dialog's
 *        focusables (-1 when focus sits outside the panel)
 * @param count number of focusable elements inside the dialog
 * @param shiftKey Shift held (reverse cycle)
 * @returns index to move focus to. Native tabbing is never relied on across
 *          the boundary: first↔last always wrap.
 */
export function resolveTabTarget(activeIndex: number, count: number, shiftKey: boolean): number {
  if (count <= 0) return -1;
  const last = count - 1;
  if (activeIndex < 0 || activeIndex > last) return shiftKey ? last : 0; // outside → pull in
  if (shiftKey) return activeIndex === 0 ? last : activeIndex - 1;
  return activeIndex === last ? 0 : activeIndex + 1;
}

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(13, 17, 23, 0.7)", // colors.bg dimmed — page falls back
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 1000,
};

const panelBase: React.CSSProperties = {
  background: cssVar("panel"),
  border: `1px solid ${cssVar("border")}`,
  borderRadius: radius.lg,
  padding: 13,
  minWidth: 280,
  maxWidth: 480,
  boxShadow: "0 8px 24px rgba(0, 0, 0, 0.5)",
};

const titleStyle: React.CSSProperties = {
  fontSize: 16, // h1 scale
  margin: "0 0 12px",
};

export function Modal({ open, onClose, title, children, className, style, testId }: ModalProps) {
  const panelRef = React.useRef<HTMLDivElement | null>(null);
  const titleId = React.useId();

  // Initial focus → first focusable control (falls back to the panel itself).
  React.useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (!panel) return;
    const first = panel.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel).focus();
  }, [open]);

  // Background scroll lock while open.
  React.useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== "Tab") return;

    const panel = panelRef.current;
    if (!panel) return;
    const focusables = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (focusables.length === 0) {
      e.preventDefault();
      return;
    }
    const activeIndex = focusables.findIndex((el) => el === document.activeElement);
    const next = resolveTabTarget(activeIndex, focusables.length, e.shiftKey);
    if (next >= 0 && next !== activeIndex) {
      e.preventDefault();
      focusables[next].focus();
    }
  };

  return (
    <div
      style={overlayStyle}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose(); // backdrop only
      }}
      onKeyDown={handleKeyDown}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={className}
        data-testid={testId}
        style={{ ...panelBase, ...style }}
      >
        <h2 id={titleId} style={titleStyle}>
          {title}
        </h2>
        {children}
      </div>
    </div>
  );
}
