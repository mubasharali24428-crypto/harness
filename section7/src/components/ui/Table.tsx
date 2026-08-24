// ui/Table — DS lane (M14-W4). The app's data-table convention (full width,
// collapsed borders, 13px, left-aligned cells with hairline row separators)
// wrapped in an overflow-x container so wide tables scroll instead of breaking
// narrow viewports (pairs with lane F2b's "tables scroll-x" requirement).
//
// A11y: real <table> semantics preserved — children are passed through
// untouched, so thead/tbody/tr/th/td and any caption/sr-only content keep
// their native roles. The wrapper is a plain div (no tabbable overflow, which
// would break keyboard scrolling); callers keep interactive controls INSIDE
// cells as buttons/links. `caption` renders as a visually-hidden table caption
// for screen readers when provided.

import * as React from "react";
import { fontSize } from "../tokens";

export interface TableProps {
  children: React.ReactNode;
  /** Screen-reader-only table caption (recommended for every data table). */
  caption?: string;
  className?: string;
  style?: React.CSSProperties;
  testId?: string;
}

/** Shared cell treatment: legacy thTdStyle (left, 6px/8px, hairline bottom). */
export const cellStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "6px 8px",
  borderBottom: "1px solid var(--border)",
};

const tableBase: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: fontSize.md,
};

/** Visually hidden but screen-reader present (same recipe as F2b's .sr-only). */
const srOnly: React.CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap",
  border: 0,
};

export function Table({ children, caption, className, style, testId }: TableProps) {
  return (
    <div
      className="table-scroll" // F2b lane's shared overflow-x wrapper (+ sticky first col)
      style={{ width: "100%", overflowX: "auto", ...style }}
      data-testid={testId !== undefined ? `${testId}-scroll` : undefined}
    >
      <table className={className} style={{ ...tableBase, ...style }}>
        {caption !== undefined && <caption style={srOnly}>{caption}</caption>}
        {children}
      </table>
    </div>
  );
}
