import type { CSSProperties } from "react";

// Shared panel/control faces for the working-area panels. Theme values are
// CSS variables so the warm-dark palette in globals.css stays the single
// runtime source of truth.

export const panelStyle: CSSProperties = {
  background: "var(--panel)",
  border: "1px solid var(--border-soft)",
  borderRadius: 12,
  padding: 18,
};

export const headingStyle: CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "1.3px",
  color: "var(--faint)",
  margin: "0 0 12px 0",
};

export const buttonStyle: CSSProperties = {
  padding: "8px 14px",
  background: "var(--accent-soft)",
  border: "1px solid rgba(var(--accent-rgb), 0.45)",
  borderRadius: 9,
  color: "var(--accent)",
  fontWeight: 600,
  cursor: "pointer",
  fontSize: 13,
  fontFamily: "inherit",
  transition: "background 0.15s ease, border-color 0.15s ease",
};

export const secondaryButtonStyle: CSSProperties = {
  ...buttonStyle,
  background: "var(--panel-2)",
  border: "1px solid var(--border)",
  color: "var(--text)",
};

export const dangerButtonStyle: CSSProperties = {
  ...buttonStyle,
  background: "rgba(var(--danger-rgb), 0.12)",
  border: "1px solid rgba(var(--danger-rgb), 0.4)",
  color: "var(--red)",
};

export const inputStyle: CSSProperties = {
  display: "block",
  width: "100%",
  marginTop: 4,
  padding: "9px 11px",
  background: "var(--input)",
  border: "1px solid var(--border-soft)",
  borderRadius: 9,
  color: "inherit",
  fontSize: 13,
  fontFamily: "inherit",
  boxSizing: "border-box",
};

/** Tinted pill. `color` may be a hex literal OR a CSS custom property — the
 *  fill/border are mixed with color-mix() so theme tokens work (M-A3: the old
 *  `${color}22` concatenation only produced valid CSS for hex literals, which
 *  forced every pill to hardcode a dark-theme colour and fail AA on paper). */
export function pillStyle(color: string): CSSProperties {
  return {
    display: "inline-block",
    padding: "2px 10px",
    borderRadius: 999,
    fontSize: 11,
    fontWeight: 500,
    background: `color-mix(in srgb, ${color} 13%, transparent)`,
    color,
    border: `1px solid color-mix(in srgb, ${color} 27%, transparent)`,
  };
}
