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
  sm: { padding: `6px ${space.cardPad}px` }, // 6px 14px — .btn box
  md: { padding: `${space.lg}px 14px` }, // matches .text-input's 10px height rhythm
};

const VARIANT_STYLE: Record<ButtonVariant, React.CSSProperties> = {
  secondary: {},
  primary: { borderColor: cssVar("green"), color: cssVar("green") },
  danger: { borderColor: cssVar("red"), color: cssVar("red") },
  accent: { background: cssVar("accent"), borderColor: cssVar("accent"), color: cssVar("bg") },
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
