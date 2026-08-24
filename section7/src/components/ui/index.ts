// ui/ — Mission 14 Wave 4, DS lane (Squad ALPHA). Shared design-system
// primitives for section7. This directory is the DS lane's EXCLUSIVE territory
// (W4 graph): feature lanes consume via `@/components/ui` but never edit it.
//
// Constraints honored by every file here:
//   • tokens come from ../tokens.ts — no page-local color/scale literals;
//   • inline styles only (no CSS imports) so vitest's node environment renders
//     them without extra config, matching the repo's SSR-string test style;
//   • runtime colors reference globals.css vars via cssVar() where possible,
//     so a future retheme of :root flows through automatically;
//   • aria-complete and keyboard-operable; focus-visible styled.

export { Pill, StatusPill } from "./Pill";
export type { PillProps } from "./Pill";
export { Button } from "./Button";
export type { ButtonProps, ButtonVariant, ButtonSize } from "./Button";
export { Card, cardTitleStyle } from "./Card";
export type { CardProps } from "./Card";
export { Table, cellStyle } from "./Table";
export type { TableProps } from "./Table";
export { Modal } from "./Modal";
export type { ModalProps } from "./Modal";
