import { describe, expect, it } from "vitest";

import { THEME_BOOT_SCRIPT, THEME_KEY, nextTheme, normalizeTheme, resolveTheme } from "./theme";

describe("theme resolution", () => {
  it("ignores unknown stored values", () => {
    expect(normalizeTheme("blue")).toBeNull();
    expect(normalizeTheme(null)).toBeNull();
    expect(normalizeTheme("light")).toBe("light");
    expect(normalizeTheme("dark")).toBe("dark");
  });

  it("prefers the stored choice over the OS", () => {
    expect(resolveTheme("light", false)).toBe("light");
    expect(resolveTheme("dark", true)).toBe("dark");
  });

  it("falls back to the OS preference, then dark", () => {
    expect(resolveTheme(null, true)).toBe("light");
    expect(resolveTheme(null, false)).toBe("dark");
  });

  it("toggles both ways", () => {
    expect(nextTheme("dark")).toBe("light");
    expect(nextTheme("light")).toBe("dark");
  });

  it("boot script reads the same key and sets both themes", () => {
    expect(THEME_BOOT_SCRIPT).toContain(THEME_KEY);
    expect(THEME_BOOT_SCRIPT).toContain('"light"');
    expect(THEME_BOOT_SCRIPT).toContain('"dark"');
    expect(THEME_BOOT_SCRIPT).toContain("prefers-color-scheme: light");
  });
});
