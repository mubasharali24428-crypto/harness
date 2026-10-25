"use client";

// Shared page states: loading, empty, failure. Before this file each page
// inlined its own tinted paragraph, which read like a debug trace at exactly
// the moments a first-time reviewer sees the product. One vocabulary here.
//
// Server-safe: pure inline styles, no effects, so route modules and the
// node-environment vitest suite can render these directly.

import * as React from "react";
import { Button } from "./ui";
import { cssVar, fontSize, radius, space } from "./tokens";

const wrap: React.CSSProperties = {
  border: `1px dashed ${cssVar("border")}`,
  borderRadius: radius.lg,
  padding: `${space.xl * 2}px ${space.lg}px`,
  margin: "12px 0",
  background: cssVar("panel"),
  color: cssVar("muted"),
};

const titleStyle: React.CSSProperties = {
  margin: "0 0 6px",
  color: cssVar("text"),
  fontSize: fontSize.md,
  fontWeight: 600,
};

const bodyStyle: React.CSSProperties = { margin: 0, fontSize: fontSize.sm, lineHeight: 1.5 };

export function LoadingState({
  label = "Loading…",
  rows = 3,
}: {
  label?: string;
  rows?: number;
}) {
  return (
    <div role="status" aria-live="polite" aria-busy="true" style={wrap} data-testid="loading-state">
      <p style={{ ...bodyStyle, marginBottom: 10 }}>{label}</p>
      <div aria-hidden="true" style={{ display: "grid", gap: 8 }}>
        {Array.from({ length: rows }, (_, i) => (
          <div
            key={i}
            style={{
              height: 10,
              width: `${92 - i * 18}%`,
              borderRadius: 5,
              background: cssVar("panel2"),
              border: `1px solid ${cssVar("border")}`,
            }}
          />
        ))}
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div style={wrap} data-testid="empty-state">
      <p style={titleStyle}>{title}</p>
      <p style={bodyStyle}>{body}</p>
      {action && <div style={{ marginTop: 12 }}>{action}</div>}
    </div>
  );
}

export function ErrorState({
  title = "That didn’t load",
  message,
  onRetry,
  retryLabel = "Try again",
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div
      role="alert"
      style={{ ...wrap, borderStyle: "solid", borderColor: cssVar("red") }}
      data-testid="error-state"
    >
      <p style={titleStyle}>{title}</p>
      <p style={bodyStyle}>{message}</p>
      {onRetry && (
        <div style={{ marginTop: 12 }}>
          <Button size="sm" onClick={onRetry}>
            {retryLabel}
          </Button>
        </div>
      )}
    </div>
  );
}
