// ui/Notice — Mission 16 Wave 6 lane X4 PROPOSAL (Squad BETA). NEW FILE: the
// components/ui/** directory is consume-only for feature lanes, so this lands
// as a proposed addition rather than an edit to any existing primitive. The
// DS lane owner can adopt it (and wire it into index.ts) or fold it elsewhere.
//
// WHY (raw-17 recurrence rule): three of the four X4-owned pages rendered
// status/error lines as bare tinted paragraphs (`color: var(--red)` on login/
// meeting/review/dashboard errors, `color: var(--green)` on the meeting
// restore note) and login added a fixed amber session-expired banner
// (M12-E2). The tinted-line pattern recurred on ALL FOUR migrated pages, so
// it is systemized here instead of being re-inlined page by page.
//
// FACES (visual parity, extracted verbatim):
//   • default: a <p> whose ink comes from the semantic tone map — exactly the
//     legacy `style={{ color: "var(--red)" }}` error/note lines. No box, no
//     padding: the surrounding page supplies rhythm.
//   • boxed: the login session-expired banner face — fixed top-center strip,
//     amber panel (#3b2f0b / #f5d76e / #8a7500). Those hexes are NOT yet in
//     tokens.ts (token gap flagged for the DS lane; raw here so the banner
//     stays pixel-identical until tokens grow an amber trio).
//
// A11y: callers choose the live-region politeness — role="alert" for
// interruptive failures, role="status" for polite notes — matching how the
// pages used these lines before (banner was role="alert"; plain error lines
// stayed silent so parity is preserved by defaulting to no role).

import * as React from "react";
import { cssVar, radius, space } from "../tokens";

export type NoticeTone = "info" | "success" | "warning" | "danger";

export interface NoticeProps {
  /** Semantic ink for the unboxed line (tone map mirrors Pill's vocabulary). */
  tone?: NoticeTone;
  /** Render the fixed amber banner face (login session-expired) instead of
   *  the bare tinted-text line. */
  boxed?: boolean;
  /** Live-region semantics: "alert" = assertive, "status" = polite. Omit for
   *  plain text (the legacy error lines carried no role). */
  role?: "status" | "alert";
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  testId?: string;
}

const TONE_INK: Record<NoticeTone, string> = {
  info: cssVar("accent"),
  success: cssVar("green"),
  warning: cssVar("yellow"),
  danger: cssVar("red"),
};

/** The M12-E2 session-expired banner, verbatim (raw hexes pending a tokens.ts
 *  amber trio — see header note). */
const BANNER_FACE: React.CSSProperties = {
  position: "fixed",
  top: 16,
  left: "50%",
  transform: "translateX(-50%)",
  zIndex: 10,
  background: "#3b2f0b",
  color: "#f5d76e",
  border: "1px solid #8a7500",
  borderRadius: radius.md,
  padding: `${space.lg}px ${space.block}px`, // 10px 18px
  fontSize: 14,
};

export function Notice({
  tone = "danger",
  boxed = false,
  role,
  children,
  className,
  style,
  testId,
  ...rest
}: NoticeProps & Omit<React.HTMLAttributes<HTMLElement>, "role" | "style" | "children">) {
  const dataProps = testId !== undefined ? { "data-testid": testId } : undefined;
  if (boxed) {
    return (
      <div
        role={role ?? "alert"}
        className={className}
        {...rest}
        {...dataProps}
        style={{ ...BANNER_FACE, ...style }}
      >
        {children}
      </div>
    );
  }
  return (
    <p role={role} className={className} {...rest} {...dataProps} style={{ color: TONE_INK[tone], ...style }}>
      {children}
    </p>
  );
}
