// Part 56: throttled announcer (fake timers), reduced-motion check, locale-aware formatters.
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createAnnouncer, prefersReducedMotion, scrollBehavior } from "./a11y";
import { parkFocus } from "./focus";
import { formatDate, formatDateTime, formatNumber, formatRelative, formatTime, timeZones } from "./intl";
import { LiveRegions } from "../components/LiveAnnouncer";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function make(over: Record<string, number> = {}) {
  vi.useFakeTimers();
  vi.setSystemTime(0);
  const polite = vi.fn();
  const assertive = vi.fn();
  const a = createAnnouncer({ polite, assertive, ...over });
  return { a, polite, assertive };
}

describe("throttled announcer", () => {
  it("the first message is spoken at once", () => {
    const { a, polite } = make();
    a.announce("Ann joined");
    expect(polite).toHaveBeenCalledTimes(1);
    expect(polite).toHaveBeenCalledWith("Ann joined");
  });

  it("a burst inside the window is coalesced into ONE message when the window closes", () => {
    const { a, polite } = make();
    a.announce("one");
    for (const t of ["two", "three", "four", "five"]) a.announce(t);
    expect(polite).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(3999);
    expect(polite).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);
    expect(polite).toHaveBeenCalledTimes(2);
    expect(polite.mock.calls[1][0]).toBe("4 updates. Latest: five");
  });

  it("never more than one polite message per window, however long the burst lasts", () => {
    const { a, polite } = make();
    for (let i = 0; i < 100; i++) { a.announce(`event ${i}`); vi.advanceTimersByTime(100); }
    vi.advanceTimersByTime(10_000);
    // 10 s of events + a tail: at most one per 4 s window, never 100
    expect(polite.mock.calls.length).toBeLessThanOrEqual(4);
    expect(polite.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("an identical sentence twice in a row is one; empty text is ignored", () => {
    const { a, polite } = make();
    a.announce("x"); a.announce("y"); a.announce("y"); a.announce("  ");
    vi.advanceTimersByTime(4000);
    expect(polite.mock.calls.map((c) => c[0])).toEqual(["x", "y"]);
  });

  it("a long queue is bounded and the overflow is still counted", () => {
    const { a, polite } = make({ maxQueued: 3 });
    a.announce("first");
    for (let i = 0; i < 10; i++) a.announce(`n${i}`);
    vi.advanceTimersByTime(4000);
    expect(polite.mock.calls[1][0]).toMatch(/^10 updates\. Latest: n2$/);
  });

  it("after a quiet spell the next message is immediate again", () => {
    const { a, polite } = make();
    a.announce("a");
    vi.advanceTimersByTime(20_000);
    a.announce("b");
    expect(polite.mock.calls.map((c) => c[0])).toEqual(["a", "b"]);
  });

  it("urgent goes to the assertive region at once, but at most once a second (newest wins)", () => {
    const { a, assertive, polite } = make();
    a.announce("Approval needed: delete src", true);
    a.announce("second", true);
    a.announce("third", true);
    expect(assertive).toHaveBeenCalledTimes(1);
    expect(assertive).toHaveBeenCalledWith("Approval needed: delete src");
    vi.advanceTimersByTime(1000);
    expect(assertive.mock.calls.map((c) => c[0])).toEqual(["Approval needed: delete src", "third"]);
    expect(polite).not.toHaveBeenCalled();
  });

  it("dispose cancels pending speech", () => {
    const { a, polite } = make();
    a.announce("a"); a.announce("b");
    a.dispose();
    vi.advanceTimersByTime(60_000);
    expect(polite).toHaveBeenCalledTimes(1);
  });
});

describe("live regions markup", () => {
  it("one polite status region and one assertive alert region, atomic", () => {
    const html = renderToStaticMarkup(React.createElement(LiveRegions, { polite: "p", assertive: "a" }));
    expect(html).toContain('role="status" aria-live="polite" aria-atomic="true"');
    expect(html).toContain('role="alert" aria-live="assertive" aria-atomic="true"');
    expect(html.match(/aria-live="polite"/g)).toHaveLength(1);
  });
});

describe("reduced motion", () => {
  it("is false without matchMedia (node) and scrolling is smooth only when motion is allowed", () => {
    expect(prefersReducedMotion()).toBe(false);
    vi.stubGlobal("window", { matchMedia: (q: string) => ({ matches: q.includes("reduce") }) });
    expect(prefersReducedMotion()).toBe(true);
    expect(scrollBehavior()).toBe("auto");
    vi.stubGlobal("window", { matchMedia: () => ({ matches: false }) });
    expect(scrollBehavior()).toBe("smooth");
  });
});

describe("locale-aware formatting (Intl, no hard-coded en-US)", () => {
  const iso = "2026-03-04T15:09:00Z";
  it("dates follow the locale and the time zone", () => {
    const us = formatDateTime(iso, { locale: "en-US", timeZone: "UTC" });
    const de = formatDateTime(iso, { locale: "de-DE", timeZone: "UTC" });
    expect(us).not.toBe(de);
    expect(de).toMatch(/04\.03\.2026/);
    expect(us).toMatch(/Mar 4, 2026/);
    expect(formatTime(iso, { locale: "en-US", timeZone: "UTC" })).toMatch(/3:09:00\s?PM/);
    expect(formatTime(iso, { locale: "de-DE", timeZone: "UTC" })).toBe("15:09:00");
    expect(formatTime(iso, { locale: "en-GB", timeZone: "Asia/Karachi" })).toBe("20:09:00");
    expect(formatDate(iso, { locale: "fr-FR", timeZone: "UTC" })).toBe("4 mars 2026");
  });
  it("numbers follow the locale", () => {
    expect(formatNumber(1234567.5, { locale: "en-US" })).toBe("1,234,567.5");
    expect(formatNumber(1234567.5, { locale: "de-DE" })).toBe("1.234.567,5");
  });
  it("relative time speaks the locale's language", () => {
    const now = Date.parse(iso);
    expect(formatRelative("2026-03-04T15:04:00Z", now, { locale: "en" })).toBe("5 minutes ago");
    expect(formatRelative("2026-03-04T15:04:00Z", now, { locale: "de" })).toMatch(/vor 5 Minuten/);
    expect(formatRelative("2026-03-04T18:09:00Z", now, { locale: "en" })).toBe("in 3 hours");
  });
  it("junk input is an em dash and a bad locale or zone never throws", () => {
    expect(formatDateTime("nonsense")).toBe("—");
    expect(formatDateTime(null)).toBe("—");
    expect(formatRelative(undefined)).toBe("—");
    expect(formatDateTime(iso, { locale: "en-US", timeZone: "Not/AZone" })).toBe("2026-03-04T15:09:00.000Z");
  });
  it("offers a time zone list", () => {
    expect(timeZones()).toContain("UTC");
  });
});

describe("parkFocus", () => {
  it("focuses the nearest data-focus-park ancestor before the clicked control goes away", () => {
    const focus = vi.fn();
    const closest = vi.fn(() => ({ focus }));
    parkFocus({ currentTarget: { closest } });
    expect(closest).toHaveBeenCalledWith("[data-focus-park]");
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
  });
  it("does nothing, and does not throw, when there is no parking spot or no element", () => {
    expect(() => parkFocus({ currentTarget: { closest: () => null } })).not.toThrow();
    expect(() => parkFocus({ currentTarget: null })).not.toThrow();
    expect(() => parkFocus({ currentTarget: {} })).not.toThrow();
  });
});
