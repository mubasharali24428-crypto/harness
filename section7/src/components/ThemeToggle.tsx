"use client";

// Sun/moon toggle. First paint matches the boot script (dark unless the
// visitor prefers light); after mount the state is read from the same
// attributes the script set, so the label always names the NEXT theme.

import * as React from "react";
import { applyTheme, nextTheme, normalizeTheme, resolveTheme, THEME_KEY, type Theme } from "@/lib/theme";
import { cssVar, fontSize, radius, space } from "./tokens";

export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const [theme, setTheme] = React.useState<Theme>("dark");

  React.useEffect(() => {
    const root = document.documentElement.dataset.theme;
    const current =
      normalizeTheme(root) ??
      resolveTheme(
        normalizeTheme(window.localStorage.getItem(THEME_KEY)),
        window.matchMedia?.("(prefers-color-scheme: light)").matches ?? false,
      );
    setTheme(current);
  }, []);

  const next = nextTheme(theme);

  return (
    <button
      type="button"
      onClick={() => {
        applyTheme(next);
        setTheme(next);
      }}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      data-testid="theme-toggle"
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 6,
        width: compact ? 32 : "100%",
        minHeight: 32,
        padding: compact ? 0 : `${space.sm}px ${space.md}px`,
        background: "transparent",
        color: cssVar("muted"),
        border: "1px solid var(--border-soft)",
        borderRadius: radius.md,
        cursor: "pointer",
        fontSize: fontSize.sm,
        fontFamily: "inherit",
      }}
    >
      <span aria-hidden="true">{theme === "dark" ? "☾" : "☀"}</span>
      {!compact && <span>{theme === "dark" ? "Dark" : "Light"} theme</span>}
    </button>
  );
}
