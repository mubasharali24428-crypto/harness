// ui/Pill — DS lane (M14-W4). Status pill over the app's four-tone semantic
// vocabulary (green/red/yellow/blue from globals.css .pill.*). Extracted from
// the `rolePillClass` / `statusPillClass` maps every page kept re-inventing.
//
// Semantics (canonical mapping lives in tokens.statusTones):
//   green = success/terminal-ok/admin · red = failure/blocked/destructive/guest
//   yellow = in-flight/warning/viewer · blue = informational/waiting/engineer
//
// A11y: renders as <span> by default (pure status indicator). When a caller
// passes onClick it becomes a real <button type="button"> so it stays
// keyboard-operable, with aria-pressed when `active` is set (toggle chips) and
// focus-visible styling. Decorative pills get no role; callers attach their
// own live semantics to containers (role="status"/"alert") as before.

import * as React from "react";
import { colors, cssVar, fontSize, radius, space, toneForStatus, type StatusTone } from "../tokens";

export interface PillProps {
  /** Visual tone. Pass a semantic string via toneForStatus() for raw states. */
  tone: StatusTone;
  children: React.ReactNode;
  /** Render as an interactive toggle-chip button instead of a static span. */
  onClick?: () => void;
  /** For interactive pills: pressed state exposed as aria-pressed. */
  active?: boolean;
  /** Accessible name for icon-only/interactive pills. */
  "aria-label"?: string;
  className?: string;
  style?: React.CSSProperties;
  testId?: string;
}

/** Tone → { border, text } pairs, exactly the globals.css .pill rules. */
const TONE_VARS: Record<StatusTone, { background: string; border: string; text: string }> = {
  green: { background: "rgba(92, 184, 112, 0.14)", border: "rgba(92, 184, 112, 0.28)", text: cssVar("green") },
  red: { background: "rgba(255, 107, 107, 0.14)", border: "rgba(255, 107, 107, 0.28)", text: cssVar("red") },
  yellow: { background: "rgba(229, 168, 20, 0.14)", border: "rgba(229, 168, 20, 0.3)", text: cssVar("yellow") },
  blue: { background: "rgba(108, 184, 255, 0.13)", border: "rgba(108, 184, 255, 0.28)", text: cssVar("accent") },
};

/** Static pill geometry — matches the legacy `.pill` class box. */
const baseStyle: React.CSSProperties = {
  display: "inline-block",
  borderRadius: radius.pill,
  fontSize: fontSize.xs,
};

/** Interactive chip geometry — matches the cost page's chipStyle(false). */
const chipStyle: React.CSSProperties = {
  ...baseStyle,
  background: cssVar("panel2"),
  border: `1px solid ${cssVar("border")}`,
  color: cssVar("text"),
  padding: "5px 12px",
  cursor: "pointer",
  font: "inherit",
};

/** Active chip face — matches chipStyle(true): accent fill + dark ink. */
const activeChipStyle: React.CSSProperties = {
  ...chipStyle,
  background: colors.accent,
  color: colors.onAccent,
};

export function Pill({ tone, children, onClick, active, className, style, testId }: PillProps) {
  const dataProps = testId !== undefined ? { "data-testid": testId } : undefined;

  if (onClick) {
    return (
      <button
        type="button"
        aria-pressed={active}
        onClick={onClick}
        className={className}
        style={{ ...(active ? activeChipStyle : chipStyle), ...style }}
        {...dataProps}
      >
        {children}
      </button>
    );
  }

  const vars = TONE_VARS[tone];
  return (
    <span
      className={className}
      style={{
        ...baseStyle,
        background: vars.background,
        border: `1px solid ${vars.border}`,
        color: vars.text,
        ...style,
      }}
      {...dataProps}
    >
      {children}
    </span>
  );
}

/** Convenience wrapper: resolve a raw status string to its Pill. Unknown
 *  statuses degrade to the neutral blue info tone (honest fallthrough). */
export function StatusPill({ status, ...rest }: Omit<PillProps, "tone"> & { status: string }) {
  return <Pill tone={toneForStatus(status)} {...rest} />;
}
