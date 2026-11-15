import { describe, expect, it } from "vitest";
import type { ReviewDto } from "@/lib/api";
import { approveBlock, commentAsDirection, reviewRisks } from "@/components/workspace/reviewFlow";

const base = { prId: "p", pr_url: "u" } as ReviewDto;

describe("reviewFlow", () => {
  it("lists recorded risk flags, else sensitive paths, else nothing", () => {
    expect(reviewRisks(null)).toEqual([]);
    expect(reviewRisks({ ...base, changedFiles: [{ path: "src/a.ts", additions: 1, deletions: 0 }] })).toEqual([]);
    expect(reviewRisks({ ...base, changedFiles: [{ path: "auth/session.ts", additions: 1, deletions: 0 }] }))
      .toEqual(["auth/session.ts: sensitive area"]);
    expect(reviewRisks({ ...base, riskFlags: [{ path: "db/migrations/1.sql", severity: "high", rule: "r", reason: "schema change" }] }))
      .toEqual(["db/migrations/1.sql: high, schema change"]);
  });

  it("a risky change cannot be approved until the reviewer confirms the flags", () => {
    const risks = ["auth/session.ts: sensitive area"];
    expect(approveBlock({ role: "engineer", risks, acknowledged: false })).toMatch(/risk flags/);
    expect(approveBlock({ role: "engineer", risks, acknowledged: true })).toBeNull();
    expect(approveBlock({ role: "engineer", risks: [], acknowledged: false })).toBeNull();
  });

  it("a viewer cannot approve, risky or not", () => {
    expect(approveBlock({ role: "viewer", risks: [], acknowledged: true })).toMatch(/role/);
  });

  it("a comment reads as direction with its file and line", () => {
    expect(commentAsDirection({ path: "a.ts", line: 12, body: "rename this" })).toBe("a.ts:12: rename this");
    expect(commentAsDirection({ path: "a.ts", line: null, body: "split it" })).toBe("a.ts: split it");
  });
});
