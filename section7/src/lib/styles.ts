import type { CSSProperties } from "react";

export const panelStyle: CSSProperties = {
  background: "#111418",
  border: "1px solid #2a3038",
  borderRadius: 8,
  padding: 16,
};

export const headingStyle: CSSProperties = {
  fontSize: 13,
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  color: "#9aa4b2",
  margin: "0 0 12px 0",
};

export const buttonStyle: CSSProperties = {
  padding: "8px 14px",
  background: "#3b82f6",
  border: "none",
  borderRadius: 6,
  color: "white",
  fontWeight: 600,
  cursor: "pointer",
  fontSize: 13,
};

export const secondaryButtonStyle: CSSProperties = {
  ...buttonStyle,
  background: "#2a3038",
};

export const dangerButtonStyle: CSSProperties = {
  ...buttonStyle,
  background: "#ef4444",
};

export const inputStyle: CSSProperties = {
  display: "block",
  width: "100%",
  marginTop: 4,
  padding: 8,
  background: "#161a20",
  border: "1px solid #2a3038",
  borderRadius: 6,
  color: "inherit",
  fontSize: 13,
  boxSizing: "border-box",
};

export function pillStyle(color: string): CSSProperties {
  return {
    display: "inline-block",
    padding: "2px 10px",
    borderRadius: 999,
    fontSize: 12,
    fontWeight: 600,
    background: `${color}22`,
    color,
    border: `1px solid ${color}55`,
  };
}
