"use client";

// M13-G5b — shared breadcrumb trail (raw-14 DX batch). Deep pages like
// /settings/team previously had no visible way back; the app's home is the
// Command Center at "/", so trails are rooted there. Presentational only:
// real anchors (the same convention the rest of section7 uses), the last
// crumb rendered as aria-current="page" text, never a link to itself.
import * as React from "react";

export interface Crumb {
  label: string;
  /** Omit for the current page — renders as non-link text with aria-current. */
  href?: string;
}

export function BreadcrumbTrail({ items }: { items: Crumb[] }) {
  return (
    <nav
      aria-label="Breadcrumb"
      data-testid="breadcrumbs"
      style={{
        marginBottom: 12,
        fontSize: 13,
        color: "var(--muted)",
        display: "flex",
        gap: 6,
        alignItems: "center",
        flexWrap: "wrap",
      }}
    >
      {items.map((c, i) => (
        <span key={`${c.label}-${i}`} style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
          {i > 0 && (
            <span aria-hidden="true" style={{ color: "var(--border)" }}>
              /
            </span>
          )}
          {c.href !== undefined ? (
            <a href={c.href} style={{ color: "var(--accent)", textDecoration: "underline" }}>
              {c.label}
            </a>
          ) : (
            <span aria-current="page">{c.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}

/** Standard trail for pages under /settings: Command Center → Settings → here. */
export function settingsCrumbs(current: string): Crumb[] {
  return [
    { label: "Command Center", href: "/" },
    { label: "Settings", href: "/settings" },
    { label: current },
  ];
}
