// R4: the Diff pane's default scope and when the snapshot history shows.
import { describe, expect, it } from "vitest";
import { DIFF_SCOPES, defaultDiffScope, showChangeReview } from "@/lib/diffScope";

describe("defaultDiffScope", () => {
  it("opens on since-last whenever there is an iteration", () => {
    expect(defaultDiffScope(true)).toBe("since-last");
  });
  it("falls back to the full pull request when nothing was snapshotted", () => {
    expect(defaultDiffScope(false)).toBe("full-pr");
  });
});

describe("showChangeReview", () => {
  it("hides the snapshot history while since-last is primary on the Diff pane", () => {
    expect(showChangeReview("diff", "since-last")).toBe(false);
  });
  it("hides it while the scope is still being decided, so it never flashes", () => {
    expect(showChangeReview("diff", null)).toBe(false);
  });
  it("shows it on the full pull request scope", () => {
    expect(showChangeReview("diff", "full-pr")).toBe(true);
  });
  it("does not gate other panes", () => {
    expect(showChangeReview("plan", null)).toBe(true);
    expect(showChangeReview(null, "since-last")).toBe(true);
  });
});

describe("DIFF_SCOPES", () => {
  it("lists since-last first, with plain labels", () => {
    expect(DIFF_SCOPES.map((s) => s.label)).toEqual(["Since last iteration", "Full pull request"]);
  });
});

// X-07 × R4: the blind-approval KPI counts "diff seen" only for a view of the
// whole change (docs/decisions/2026-11-25-blind-approval-diff-seen-DRAFT.md).
import { diffViewCountsAsSeen, sinceLastCoversFullPr } from "@/lib/diffScope";

const PR_DIFF = [
  "diff --git a/src/a.ts b/src/a.ts",
  "index 111..222 100644",
  "--- a/src/a.ts",
  "+++ b/src/a.ts",
  "@@ -1,2 +1,2 @@",
  " keep",
  "-old a",
  "+new a",
  "@@ -10,1 +10,2 @@",
  " ten",
  "+eleven",
  "diff --git a/src/b.ts b/src/b.ts",
  "new file mode 100644",
  "--- /dev/null",
  "+++ b/src/b.ts",
  "@@ -0,0 +1 @@",
  "+export const b = 1;",
].join("\n");

const hunk = (lines: string[]) => ({ lines });
const fileA = { path: "src/a.ts", binary: false, hunks: [hunk([" keep", "-old a", "+new a"]), hunk([" ten", "+eleven"])] };
const fileB = { path: "src/b.ts", binary: false, hunks: [hunk(["+export const b = 1;"])] };

describe("sinceLastCoversFullPr", () => {
  it("is true when every PR file and hunk is in the since-last view", () => {
    expect(sinceLastCoversFullPr(PR_DIFF, [fileA, fileB])).toBe(true);
  });
  it("is false when since-last shows only the newest hunks", () => {
    expect(sinceLastCoversFullPr(PR_DIFF, [fileB])).toBe(false);
    expect(sinceLastCoversFullPr(PR_DIFF, [{ ...fileA, hunks: [fileA.hunks[1]] }, fileB])).toBe(false);
  });
  it("is false when a hunk's changed lines differ", () => {
    const edited = { ...fileA, hunks: [hunk([" keep", "-old a", "+newer a"]), fileA.hunks[1]] };
    expect(sinceLastCoversFullPr(PR_DIFF, [edited, fileB])).toBe(false);
  });
  it("is false with nothing to compare (no PR diff, no files)", () => {
    expect(sinceLastCoversFullPr("", [fileA])).toBe(false);
    expect(sinceLastCoversFullPr(undefined, [fileA])).toBe(false);
    expect(sinceLastCoversFullPr(PR_DIFF, [])).toBe(false);
    expect(sinceLastCoversFullPr(PR_DIFF, null)).toBe(false);
  });
  it("names a deleted file by its old path", () => {
    const del = ["--- a/gone.ts", "+++ /dev/null", "@@ -1 +0,0 @@", "-bye"].join("\n");
    expect(sinceLastCoversFullPr(del, [{ path: "gone.ts", binary: false, hunks: [hunk(["-bye"])] }])).toBe(true);
    expect(sinceLastCoversFullPr(del, [{ path: "other.ts", binary: false, hunks: [hunk(["-bye"])] }])).toBe(false);
  });
  it("needs a binary PR file to be in the since-last view too", () => {
    const bin = "diff --git a/i.png b/i.png\nBinary files a/i.png and b/i.png differ";
    expect(sinceLastCoversFullPr(bin, [{ path: "i.png", binary: true, hunks: [] }])).toBe(true);
    expect(sinceLastCoversFullPr(bin, [fileA])).toBe(false);
  });
  it("allows extra since-last files the PR no longer has", () => {
    const extra = { path: "scratch.txt", binary: false, hunks: [hunk(["+tmp"])] };
    expect(sinceLastCoversFullPr(PR_DIFF, [fileA, fileB, extra])).toBe(true);
  });
});

describe("diffViewCountsAsSeen", () => {
  it("counts the full pull request view", () => {
    expect(diffViewCountsAsSeen("full-pr", false)).toBe(true);
  });
  it("does not count since-last unless it covered every hunk", () => {
    expect(diffViewCountsAsSeen("since-last", false)).toBe(false);
    expect(diffViewCountsAsSeen("since-last", true)).toBe(true);
  });
  it("does not count while the scope is undecided", () => {
    expect(diffViewCountsAsSeen(null, true)).toBe(false);
  });
});
