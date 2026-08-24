// ui/Brand — Mission 16 Wave 6 lane X4 PROPOSAL (Squad BETA). NEW FILE: the
// components/ui/** directory is consume-only for feature lanes, so this lands
// as a proposed addition rather than an edit to any existing primitive.
//
// WHY (raw-17 recurrence rule): the HARNESS wordmark (`color: var(--accent)`,
// `letterSpacing: 2`) was inlined three times on the login page alone — one
// inline style per form mode. The .brand CSS class already exists in
// globals.css (accent ink + 2px tracking, bold); this primitive gives pages a
// token-sourced way to keep that exact face without re-inlining it.
//
// FACE: h2 with accent ink and wide tracking — the legacy login wordmark line,
// pixel-identical to the inlined original.

import * as React from "react";
import { cssVar, letterSpacing } from "../tokens";

export interface BrandProps {
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}

export function Brand({ children, className, style }: BrandProps) {
  return (
    <h2 className={className} style={{ color: cssVar("accent"), letterSpacing: letterSpacing.wider, ...style }}>
      {children}
    </h2>
  );
}
