// tokens.ts — Mission 14 Wave 4, DS lane (Squad ALPHA). Design-token source of
// truth for section7's UI primitives (src/components/ui/**).
//
// EXTRACTION DISCIPLINE (raw-17 systemization): every value below was lifted
// from the app's EXISTING visual language — app/globals.css `:root` vars and
// the repeated inline styles across pages. Nothing here invents a new palette.
// The brief's "light theme / #1F2635 header / purple accent" sketch does NOT
// match the shipped app (dark GitHub-dark palette, blue accent); per the
// mission ("read globals.css first and extract what EXISTS") the real system
// wins and is captured here instead.
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
  /** Page background (warm charcoal, #1a1814). */
  bg: "#1a1814",
  /** Sidebar/cards surface (#1e1c18). */
  panel: "#1e1c18",
  /** Elevated inputs/selects/buttons surface (#252220). */
  panel2: "#252220",
  /** Hairline borders (#3d3930). */
  border: "#3d3930",
  /** Softer card/dividing borders (#2d2a22). */
  borderSoft: "#2d2a22",
  /** Primary text (cream, #faf9f6). */
  text: "#faf9f6",
  /** Secondary text (#a8a296). */
  muted: "#a8a296",
  /** Readable body-on-panel text (#c9c4b8). */
  text2: "#c9c4b8",
  /** Mint accent (#8ee5a1). Drives links, focus rings, active nav. */
  accent: "#8ee5a1",
  /** Success/positive (#5cb870). */
  green: "#5cb870",
  /** Danger/destructive (#ff6b6b). */
  red: "#ff6b6b",
  /** Warning/caution (#e5a814). */
  yellow: "#e5a814",
  /** Ink used on accent-filled chips (warm charcoal). */
  onAccent: "#1a1814",
  /** Soft mint ink for secondary accents (#b7f0c4). */
  accentSoft: "#b7f0c4",
} as const;

/** A color role key into {@link colors}. */
export type ColorName = keyof typeof colors;

// ─── Typography ─────────────────────────────────────────────────────────────

export const font = {
  /** globals.css body font stack (monospace terminal aesthetic). */
  mono: 'ui-monospace, SFMono-Regular, Menlo, monospace',
} as const;

export const fontSize = {
  /** Micro labels: card titles (11px), event timestamps, pills. */
  xs: 11,
  /** Small helper text (12–13px body). */
  sm: 12,
  /** Default body/table text. */
  md: 14,
  /** KPI numerals on dashboard cards (22px). */
  xl: 22,
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

// ─── Space scale (px) — observed paddings/gaps/margins across globals.css
//     and page inline styles. Every stop is a value the app already uses. ────

export const space = {
  /** 1px hairlines. */
  px: 1,
  /** Tight gaps (pill rows, icon spacing). */
  xs: 4,
  /** Small gaps (chip clusters). */
  sm: 6,
  /** Default component gap (form rows, chat bubbles). */
  md: 8,
  /** Roomy gaps (chat-input, bar margins, card-title bottom margin). */
  lg: 10,
  /** Wide gaps (page header rows, minted-token top margin). */
  xl: 12,
  /** .card / explainer-box inner padding (18px). */
  cardPad: 18,
  /** Horizontal padding inside button-like controls (14px). */
  controlPadX: 14,
  /** Sidebar + elevated-container padding (16px). */
  gutter: 16,
  /** Mid-card section separation (team page h2 marginTop). */
  block: 18,
  /** .content vertical padding (20px). */
  section: 20,
  /** .content horizontal padding (24px). */
  sectionX: 24,
} as const;

// ─── Radius scale — the three corner treatments the app actually uses ───────

export const radius = {
  /** Inputs, selects, buttons, nav items (6px). */
  md: 8,
  /** Cards, panels, explainer boxes, chat bubbles (12px). */
  lg: 12,
  /** Pills/chips (999px = fully rounded). */
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
  };
  return `var(--${kebab[name] ?? name})`;
}
