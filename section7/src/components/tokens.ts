// tokens.ts — design-token source of truth for section7's UI primitives
// (src/components/ui/** and the R1 preview workspace).
//
// R1 (ui-master-prompt §SECTION R1): the final token set. Raw hexes live only
// in app/globals.css (`:root` + light block); this file mirrors the SAME names
// and adds the compile-time scales (four type sizes, 4px space, radii). The
// comments below describe intent, not history.
//
// LAYERING: raw hexes live ONLY in this file. Components/pages consume
// `cssVar(...)` or the exported CSSProperties fragments; globals.css keeps
// serving plain CSS via its own custom properties of the same names.

import type { CSSProperties } from "react";

// ─── Color: raw values (verbatim from globals.css :root) ────────────────────

/**
 * Raw palette, 1:1 with globals.css `--*` declarations. Keys mirror the CSS
 * custom-property names so a token and its var can never drift apart.
 */
export const colors = {
  /** Page background (near-black neutral). */
  bg: "#0c0d0f",
  /** Sidebar surface (darkest of the three panes). */
  panel: "#141518",
  /** Elevated inputs/selects/buttons surface. */
  panel2: "#1a1c20",
  /** Hairline borders. */
  border: "#2a2d33",
  /** Softer card/dividing borders. */
  borderSoft: "#1e2126",
  /** Primary text. */
  text: "#ededef",
  /** Secondary text on panel. */
  muted: "#8f929a",
  /** Readable body-on-panel text. */
  text2: "#b8bac1",
  /** The one accent (cool blue). Primary actions and focus only. */
  accent: "#6e9bff",
  /** Deep accent — solid fills where white text must clear AA. */
  accentStrong: "#3b6fe0",
  /** Success/positive. */
  green: "#5ecf8f",
  /** Danger/destructive. */
  red: "#ff7b7b",
  /** Warning/caution. */
  yellow: "#e3b341",
  /** Info. */
  blue: "#6cb6ff",
  /** Ink used on accent-filled chips. */
  onAccent: "#0c0d0f",
  /** Soft accent ink for secondary accents. */
  accentSoft: "#b9ccff",
} as const;

/** A color role key into {@link colors}. */
export type ColorName = keyof typeof colors;

// ─── Typography ─────────────────────────────────────────────────────────────

export const font = {
  /** Monospace stack — code, paths, terminal output, money only. */
  mono: 'var(--font-mono)',
} as const;

/** Four sizes. Nothing else in the UI may invent a fifth. */
export const fontSize = {
  /** Micro labels: card titles, event meta, pills. */
  xs: 11,
  /** Secondary text, sidebar rows, diff code. */
  sm: 13,
  /** Default body text, run goal, verdict values. */
  md: 15,
  /** Page/section headline. */
  xl: 20,
} as const;

export const fontWeight = {
  regular: 400,
  /** .brand weight. */
  bold: 700,
} as const;

export const letterSpacing = {
  /** Uppercase micro-labels (.card h2): "1px". */
  wide: "1px",
  /** .brand: "2px". */
  wider: "2px",
} as const;

/** Type-size → CSS var bridge for new components (var survives a theme swap). */
export const typeSize = {
  xs: "var(--text-xs)",
  sm: "var(--text-sm)",
  md: "var(--text-md)",
  lg: "var(--text-lg)",
} as const;

// ─── Space scale (px) — observed paddings/gaps/margins across globals.css
//     and page inline styles. Every stop is a value the app already uses. ────

/** The 4px space scale. */
export const space = {
  /** 1px hairlines. */
  px: 1,
  /** 4px: tight gaps, icon spacing. */
  xs: 4,
  /** 8px: default component gap. */
  sm: 8,
  /** 12px: roomy gaps inside a block. */
  md: 12,
  /** 16px: card padding, group separation. */
  lg: 16,
  /** 24px: section separation. */
  xl: 24,
  /** 32px: page gutters. */
  xxl: 32,
  /** Card inner padding. */
  cardPad: 16,
  /** Horizontal padding inside button-like controls. */
  controlPadX: 14,
  /** Sidebar + pane padding. */
  gutter: 16,
  /** Legacy alias for the 24px stop. */
  section: 24,
  /** Legacy alias for the 32px stop. */
  sectionX: 32,
  /** Legacy alias for the 18px mid-card stop still used by existing pages. */
  block: 18,
} as const;

// ─── Radius scale — the three corner treatments the app actually uses ───────

export const radius = {
  /** Inputs, selects, buttons, nav items. */
  md: 6,
  /** Cards, panels, the diff pane. */
  lg: 10,
  /** Pills/chips. */
  pill: 999,
} as const;

// ─── Semantic status → color-role mapping ───────────────────────────────────
//
// The app renders state through four pill colors. This is the canonical
// mapping (assembled from runs/page.tsx statusPillClass vocabulary, team
// roles, and billing/keys usage) that <Pill tone=...> consumes.

/** The four status tones Pill understands. */
export type StatusTone = "green" | "red" | "yellow" | "blue";

/** Tone used for each value when a caller passes a raw status string. */
export const statusTones: Record<string, StatusTone> = {
  // Orchestrator AgentState vocabulary (section4 state_machine.py).
  idle: "blue",
  planning: "yellow",
  awaiting_plan_approval: "blue",
  executing: "yellow",
  paused: "yellow",
  awaiting_done_signal: "blue",
  reviewing: "yellow",
  blocked: "red",
  pr_opened: "blue",
  done: "green",
  // Team roster vocabulary.
  admin: "green",
  engineer: "blue",
  viewer: "yellow",
  guest: "red",
  // Invite lifecycle vocabulary (F2a): draft waits on the sender, sent is a
  // live invitation, redeemed is terminal success, expired terminal failure,
  // revoked killed by an admin or superseded by a resend.
  draft: "yellow",
  sent: "blue",
  redeemed: "green",
  expired: "yellow",
  revoked: "red",
};

/**
 * Resolve a semantic tone for an arbitrary status string. Known values map
 * through {@link statusTones}; unknown values degrade to the neutral/info
 * accent rather than crashing — same honest fallthrough the old
 * `rolePillClass[role] ?? "pill blue"` pattern had.
 */
export function toneForStatus(status: string): StatusTone {
  return statusTones[status] ?? "blue";
}

// ─── CSS-var bridge ─────────────────────────────────────────────────────────

/**
 * Reference any token AS a CSS custom property, e.g. `color: cssVar("accent")`
 * → `var(--accent)`. Keeps runtime theming live (globals.css stays the runtime
 * source of truth) while this file remains the compile-time catalog.
 */
export function cssVar(name: ColorName): string {
  const kebab: Partial<Record<ColorName, string>> = {
    panel2: "panel-2",
    text2: "text-2",
    borderSoft: "border-soft",
    accentStrong: "accent-strong",
  };
  return `var(--${kebab[name] ?? name})`;
}
