// M13-G5b — shared client formatters (src/lib/format.ts).
// The runs page previously owned its own relative-time helper while settings
// and checkpoints rendered raw toLocaleString(); this module is the ONE
// date-fns-free formatter both vocabularies share.
import { describe, expect, it } from "vitest";
import { formatRelativeTime, formatTimestamp, teamNameSlug } from "@/lib/format";

describe("formatRelativeTime (shared)", () => {
  const NOW = Date.parse("2026-08-23T17:00:00Z");

  it("keeps the runs-page vocabulary for past timestamps", () => {
    expect(formatRelativeTime("2026-08-23T16:54:30Z", NOW)).toBe("5m ago");
    expect(formatRelativeTime("2026-08-23T15:00:00Z", NOW)).toBe("2h ago");
    expect(formatRelativeTime("2026-08-21T17:00:00Z", NOW)).toBe("2d ago");
    expect(formatRelativeTime("2026-08-23T16:59:58Z", NOW)).toBe("just now");
  });

  it("renders future timestamps as 'in X'", () => {
    expect(formatRelativeTime("2026-08-23T17:05:00Z", NOW)).toBe("in 5m");
  });

  it("renders null and garbage as an em dash — never a fabricated time", () => {
    expect(formatRelativeTime(null, NOW)).toBe("—");
    expect(formatRelativeTime("not-a-date", NOW)).toBe("—");
  });
});

describe("formatTimestamp (shared)", () => {
  it("localizes an ISO timestamp to the machine's locale string", () => {
    const iso = "2026-08-23T12:00:00Z";
    expect(formatTimestamp(iso)).toBe(new Date(iso).toLocaleString());
  });

  it("falls back to the raw string when unparseable, and '—' when null", () => {
    expect(formatTimestamp(null)).toBe("—");
    expect(formatTimestamp("garbage")).toBe("garbage");
  });

  it("is the same implementation the runs table consumes (re-export pin)", async () => {
    const { formatRelativeTime: viaRunsApi } = await import("../../app/runs/runsApi");
    const { formatRelativeTime } = await import("@/lib/format");
    expect(viaRunsApi).toBe(formatRelativeTime);
  });
});

describe("teamNameSlug (shared)", () => {
  it("slugifies team names for export filenames", () => {
    expect(teamNameSlug("Acme Robotics")).toBe("acme-robotics");
    expect(teamNameSlug("  R&D Team #2! ")).toBe("r-d-team-2");
    expect(teamNameSlug("Ünicode Team")).toBe("nicode-team");
  });

  it("collapses blank names to '' so callers can fall back", () => {
    expect(teamNameSlug("")).toBe("");
    expect(teamNameSlug("   ")).toBe("");
  });

  it("caps length so filenames stay sane", () => {
    const slug = teamNameSlug("x".repeat(100));
    expect(slug.length).toBeLessThanOrEqual(40);
  });
});
