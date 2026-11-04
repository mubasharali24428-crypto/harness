// ui/Button — DS lane (M14-W4). The `.btn` family from globals.css as a typed
// component: variant (secondary | primary | danger) × size (sm = the 6px/14px
// default, md = the 10px form-input-height match). Variants keep the exact
// legacy faces — panel-2 fill + colored border/text, no invented gradients.
//
// A11y: renders a native <button type="button"> unless `type` is given (callers
// pass "submit" inside forms), forwards disabled natively. Focus rings come
// from the GLOBAL :focus-visible rule in globals.css (F1b lane) — no JS
// focus-detection here, so mouse clicks never paint a ring and keyboard tabs
// always do. Loading swaps the label for an aria-busy state with the pending
// text announced.

import * as React from "react";
import { cssVar, fontSize, radius, space } from "../tokens";

export type ButtonVariant = "secondary" | "primary" | "danger" | "accent";
export type ButtonSize = "sm" | "md";

export interface ButtonProps {
  /** Visual weight. secondary = plain .btn, primary = green action,
   *  danger = red destructive, accent = solid accent fill (chips/CTAs). */
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Replaces children while a mutation is in flight; sets aria-busy and
   *  disables the button (matches "Removing…"-style flows). */
  loading?: boolean;
  /** Label shown while loading; falls back to the current children. */
  loadingText?: string;
  children: React.ReactNode;
  type?: "button" | "submit";
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void;
  onMouseEnter?: (e: React.MouseEvent<HTMLButtonElement>) => void;
  disabled?: boolean;
  className?: string;
  style?: React.CSSProperties;
  testId?: string;
}

const SIZE_STYLE: Record<ButtonSize, React.CSSProperties> = {
  sm: { padding: `7px ${space.controlPadX}px` }, // 7px 14px — .btn box
  md: { padding: `${space.lg}px ${space.controlPadX}px` }, // matches the input height rhythm
};

const VARIANT_STYLE: Record<ButtonVariant, React.CSSProperties> = {
  secondary: {},
  // Solid accent: the one focal action on a surface. --accent-strong clears AA
  // against white (4.86:1), which the softer accent does not.
  primary: {
    background: "linear-gradient(180deg, #4f6ff2, var(--accent-strong))",
    borderColor: "rgba(var(--accent-rgb), 0.55)",
    color: "#ffffff",
    boxShadow: "0 6px 18px rgba(69, 102, 232, 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.22)",
  },
  danger: {
    background: "rgba(var(--danger-rgb), 0.12)",
    borderColor: "rgba(var(--danger-rgb), 0.4)",
    color: cssVar("red"),
  },
  accent: { background: cssVar("accentStrong"), borderColor: cssVar("accentStrong"), color: "#ffffff" },
};

const DISABLED_STYLE: React.CSSProperties = {
  opacity: 0.55,
  cursor: "not-allowed",
};

export function Button({
  variant = "secondary",
  size = "sm",
  loading = false,
  loadingText,
  children,
  type = "button",
  onClick,
  onMouseEnter,
  disabled = false,
  className,
  style,
  testId,
  ...rest
}: ButtonProps & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "style" | "className" | "children" | "type" | "onClick" | "onMouseEnter" | "disabled">) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      className={className}
      {...rest}
      data-testid={testId}
      style={{
        background: cssVar("panel2"),
        border: `1px solid ${cssVar("border")}`,
        color: cssVar("text"),
        borderRadius: radius.md,
        cursor: "pointer",
        font: "inherit",
        fontWeight: 500,
        transition: "background 0.15s ease, border-color 0.15s ease",
        ...SIZE_STYLE[size],
        ...VARIANT_STYLE[variant],
        ...(disabled || loading ? DISABLED_STYLE : undefined),
        ...style,
      }}
    >
      {loading ? (loadingText ?? children) : children}
    </button>
  );
}
