import { describe, expect, it } from "vitest";
import { NAV_COMMANDS, isTypingTarget, stepShortcut } from "@/lib/commands";

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
  it("never fires while the user is typing", () => {
    expect(isTypingTarget("input", false)).toBe(true);
    expect(isTypingTarget("TEXTAREA", false)).toBe(true);
    expect(isTypingTarget("div", true)).toBe(true);
    expect(isTypingTarget("div", false)).toBe(false);
    expect(isTypingTarget(undefined, false)).toBe(false);
  });
});
