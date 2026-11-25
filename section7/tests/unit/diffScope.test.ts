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
