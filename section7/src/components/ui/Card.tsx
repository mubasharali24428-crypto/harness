// ui/Card — DS lane (M14-W4). The `.card` surface from globals.css (panel
// background, 1px border, 8px radius, 14px padding) with its uppercase micro-
// label heading convention baked in. `title` renders the h2 exactly like the
// legacy `.card h2` rule; omit it for KPI tiles that lay out their own content.
//
// A11y: section + heading by default so cards are landmark-navigable;
// as={div} opts out for nested-card layouts. Callers pass aria-label for
// icon-only/label-less sections.

import * as React from "react";
import { cssVar, fontSize, letterSpacing, radius, space } from "../tokens";

export interface CardProps {
  /** Uppercase micro-label rendered as the card's heading (legacy .card h2). */
  title?: React.ReactNode;
  /** Overrides for the title h2 (e.g. { marginBottom: 0 } beside a header row). */
  titleStyle?: React.CSSProperties;
  children: React.ReactNode;
  /** Element to render. "section" (default) keeps landmark semantics. */
  as?: "section" | "div";
  /** Accessible name when there is no visible title. */
  "aria-label"?: string;
  className?: string;
  style?: React.CSSProperties;
  testId?: string;
}

/** The .card rule, tokenized. */
const cardBase: React.CSSProperties = {
  background: cssVar("panel"),
  border: `1px solid ${cssVar("border")}`,
  borderRadius: radius.lg,
  padding: space.cardPad,
};

/** The .card h2 rule, tokenized. */
export const cardTitleStyle: React.CSSProperties = {
  fontSize: fontSize.xs,
  letterSpacing: letterSpacing.wide,
  textTransform: "uppercase",
  color: cssVar("muted"),
  margin: `0 0 ${space.lg}px`,
};

export function Card({
  title,
  titleStyle,
  children,
  as = "section",
  className,
  style,
  testId,
  ...aria
}: CardProps & React.HTMLAttributes<HTMLElement>) {
  const Tag = as;
  return (
    <Tag className={className ?? "card"} data-testid={testId} {...aria} style={{ ...cardBase, ...style }}>
      {title !== undefined && <h2 style={{ ...cardTitleStyle, ...titleStyle }}>{title}</h2>}
      {children}
    </Tag>
  );
}
