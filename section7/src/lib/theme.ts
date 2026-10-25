// Theme selection: one stored preference, dark by default, OS preference
// honoured only until the visitor makes a choice. The boot script below runs
// before first paint so a light-theme visitor never sees a dark flash.

export type Theme = "dark" | "light";

export const THEME_KEY = "harness.theme";

/** Injected into <head>; mirrors resolveTheme() without importing React. */
export const THEME_BOOT_SCRIPT = `
try {
  var stored = localStorage.getItem("${THEME_KEY}");
  var light = stored === "light" || (stored !== "dark" &&
    window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches);
  document.documentElement.dataset.theme = light ? "light" : "dark";
} catch (e) {
  document.documentElement.dataset.theme = "dark";
}
`;

export function normalizeTheme(value: string | null | undefined): Theme | null {
  return value === "light" || value === "dark" ? value : null;
}

export function resolveTheme(stored: string | null, prefersLight: boolean): Theme {
  return normalizeTheme(stored) ?? (prefersLight ? "light" : "dark");
}

export function nextTheme(theme: Theme): Theme {
  return theme === "dark" ? "light" : "dark";
}

/** Client-only: persist the choice and flip the root attribute. */
export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* private mode: the attribute still applies for this page */
  }
}
