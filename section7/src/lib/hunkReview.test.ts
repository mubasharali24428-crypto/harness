import { describe, expect, it } from "vitest";

import type { CheckpointFile } from "./api";
import {
  hunkKey,
  hunkLabel,
  lineTone,
  parseHunkKey,
  selectionsFromKeys,
  summarize,
  summaryLine,
  toggleKey,
} from "./hunkReview";

describe("hunk identity", () => {
  it("round-trips paths that contain colons", () => {
    const key = hunkKey("src/a:b.py", 3);
    expect(key).toBe("src/a:b.py:3");
    expect(parseHunkKey(key)).toEqual({ path: "src/a:b.py", index: 3 });
  });

  it("rejects malformed keys instead of guessing", () => {
    expect(parseHunkKey("nocolon")).toBeNull();
    expect(parseHunkKey(":2")).toBeNull();
    expect(parseHunkKey("a.py:x")).toBeNull();
    expect(parseHunkKey("a.py:-1")).toBeNull();
  });

  it("toggles without mutating the previous set", () => {
    const before = new Set(["a:0"]);
    const after = toggleKey(before, "a:0");
    expect(before.has("a:0")).toBe(true);
    expect(after.has("a:0")).toBe(false);
  });

  it("builds the wire payload only from valid keys", () => {
    expect(selectionsFromKeys(["a.py:0", "bad", "b.py:2"])).toEqual([
      { path: "a.py", index: 0 },
      { path: "b.py", index: 2 },
    ]);
  });
});

const files: CheckpointFile[] = [
  {
    path: "src/a.py",
    binary: false,
    hunks: [
      { index: 0, header: "@@ -1,2 +1,3 @@", lines: [" x", "-y", "+z"], additions: 1, deletions: 1 },
      { index: 1, header: "@@ -20,2 +20,2 @@ def f()", lines: [" a", "-b", "+c"], additions: 1, deletions: 1 },
    ],
  },
  {
    path: "logo.png",
    binary: true,
    hunks: [],
  },
];

describe("diff summary", () => {
  it("counts files, hunks and line deltas", () => {
    expect(summarize(files)).toEqual({ files: 2, hunks: 2, additions: 2, deletions: 2 });
  });

  it("names binary files instead of pretending they have hunks", () => {
    expect(summaryLine(files)).toBe("2 files · 2 hunks · +2 −2 · 1 binary");
    expect(summaryLine([files[0]])).toBe("1 file · 2 hunks · +2 −2");
  });
});

describe("line tones and labels", () => {
  it("maps diff prefixes to tones, headers win over +/-", () => {
    expect(lineTone("+++ b/x")).toBe("meta");
    expect(lineTone("--- a/x")).toBe("meta");
    expect(lineTone("+added")).toBe("add");
    expect(lineTone("-removed")).toBe("del");
    expect(lineTone(" context")).toBe("context");
    expect(lineTone("\\ No newline at end of file")).toBe("meta");
  });

  it("turns a hunk header into a line-range label", () => {
    expect(hunkLabel(files[0].hunks[1])).toBe("-20,2 +20,2 def f()");
  });
});
