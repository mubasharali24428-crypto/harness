import { describe, expect, it } from "vitest";
import { CATALOGS, EN, LOCALES, dirFor, formatDateTime, formatRelative, isValidTimeZone, makeT, parseLocale } from "../../src/lib/i18n";

const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

describe("i18n catalogs", () => {
  it("every locale has exactly the English keys with the same placeholders and no empty strings", () => {
    for (const l of LOCALES) {
      expect(Object.keys(CATALOGS[l]).sort(), l).toEqual(Object.keys(EN).sort());
      for (const [k, v] of Object.entries(CATALOGS[l])) {
        expect(v.trim().length, `${l}.${k}`).toBeGreaterThan(0);
        expect(placeholders(v), `${l}.${k}`).toEqual(placeholders(EN[k as keyof typeof EN]));
      }
    }
  });
  it("interpolates, leaves unknown placeholders visible, and falls back to English for a missing key", () => {
    expect(makeT("es")("inbox.waiting", { n: 3 })).toBe("3 esperando una decisión");
    expect(makeT("en")("inbox.approveLabel")).toBe("Approve {title}");
    const broken = { ...CATALOGS.es } as Record<string, string>;
    delete broken["inbox.title"];
    const saved = CATALOGS.es;
    (CATALOGS as Record<string, unknown>).es = broken;
    expect(makeT("es")("inbox.title")).toBe("Task inbox");
    (CATALOGS as Record<string, unknown>).es = saved;
  });
  it("parses locales safely and sets direction", () => {
    expect(parseLocale("ar-EG")).toBe("ar");
    expect(parseLocale("fr")).toBe("en");
    expect(parseLocale(null)).toBe("en");
    expect(dirFor("ar")).toBe("rtl");
    expect(dirFor("es")).toBe("ltr");
  });
});

describe("timezone-correct formatting", () => {
  const iso = "2026-03-08T06:30:00Z"; // spans the US DST switch day
  it("renders the same instant in each user's zone", () => {
    expect(formatDateTime(iso, "en", "UTC")).toContain("6:30");
    expect(formatDateTime(iso, "en", "Asia/Karachi")).toContain("11:30");
    expect(formatDateTime(iso, "en", "America/New_York")).toMatch(/1:30\s?AM/);
    expect(formatDateTime("2026-03-08T08:30:00Z", "en", "America/New_York")).toMatch(/4:30\s?AM/); // after spring-forward
  });
  it("invalid zone falls back to UTC; bad input is never fabricated", () => {
    expect(isValidTimeZone("Mars/Base")).toBe(false);
    expect(formatDateTime(iso, "en", "Mars/Base")).toContain("UTC");
    expect(formatDateTime("not-a-date", "en", "UTC")).toBe("not-a-date");
    expect(formatDateTime(null, "en", "UTC")).toBe("—");
  });
  it("localises relative time", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(formatRelative("2026-01-01T11:00:00Z", "en", now)).toBe("1 hour ago");
    expect(formatRelative("2026-01-01T11:00:00Z", "es", now)).toBe("hace 1 hora");
    expect(formatRelative("nope", "en", now)).toBe("—");
  });
});
