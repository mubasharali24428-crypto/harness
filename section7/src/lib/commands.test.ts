import { describe, expect, it } from "vitest";
import { readdirSync } from "node:fs";
import { TOUR_KEYS } from "@/components/KeyTour";
import { join } from "node:path";
import { NAV_COMMANDS, OTHER_PAGES, SETTINGS_PAGES, isTypingTarget, stepShortcut } from "@/lib/commands";

describe("command shortcuts (U0)", () => {
  it("g then a letter opens that route, and only after g", () => {
    expect(stepShortcut(false, "r")).toEqual({ href: null, pending: false });
    expect(stepShortcut(false, "g")).toEqual({ href: null, pending: true });
    expect(stepShortcut(true, "r")).toEqual({ href: "/runs", pending: false });
    expect(stepShortcut(true, "R")).toEqual({ href: "/runs", pending: false });
  });
  it("an unknown second key cancels instead of sticking", () => {
    expect(stepShortcut(true, "z")).toEqual({ href: null, pending: false });
  });
  it("every command has a unique id, key and route", () => {
    for (const f of ["id", "key", "href"] as const) {
      expect(new Set(NAV_COMMANDS.map((c) => c[f])).size).toBe(NAV_COMMANDS.length);
    }
    expect(NAV_COMMANDS.some((c) => c.key === "g")).toBe(false);
  });
  it("the palette reaches every static page of the app", () => {
    const pages: string[] = [];
    const walk = (dir: string, route: string) => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        if (e.isDirectory() && !e.name.startsWith("[")) walk(join(dir, e.name), `${route}/${e.name}`);
        else if (e.name === "page.tsx") pages.push(route || "/");
      }
    };
    walk(join(process.cwd(), "app"), "");
    // Not destinations a signed-in person navigates to.
    const skip = ["/login", "/auth/sso/callback", "/notifications/unsubscribe"];
    const reachable = new Set([...NAV_COMMANDS, ...SETTINGS_PAGES, ...OTHER_PAGES].map((c) => c.href));
    expect(pages.filter((p) => !skip.includes(p) && !reachable.has(p))).toEqual([]);
  });
  it("the key tour teaches five keys, and its g-shortcuts are real", () => {
    expect(TOUR_KEYS).toHaveLength(5);
    for (const k of TOUR_KEYS.filter((t) => t.keys[0] === "g")) {
      expect(stepShortcut(true, k.keys[1]).href).not.toBeNull();
    }
  });
  it("never fires while the user is typing", () => {
    expect(isTypingTarget("input", false)).toBe(true);
    expect(isTypingTarget("TEXTAREA", false)).toBe(true);
    expect(isTypingTarget("div", true)).toBe(true);
    expect(isTypingTarget("div", false)).toBe(false);
    expect(isTypingTarget(undefined, false)).toBe(false);
  });
});
