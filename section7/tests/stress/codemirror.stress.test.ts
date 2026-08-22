import { describe, it, expect } from "vitest";
import { diffLineClass } from "@/lib/codemirror";

describe("diffLineClass - Edge Cases", () => {
  it("correctly identifies diff hunk header lines", () => {
    expect(diffLineClass("@@ -10,5 +10,6 @@")).toBe("cm-diff-hunk");
    expect(diffLineClass("@@ -0,0 +1,5 @@ function test()")).toBe("cm-diff-hunk");
  });

  it("correctly identifies file headers before + and - operations", () => {
    expect(diffLineClass("--- a/src/index.ts")).toBe("cm-diff-file");
    expect(diffLineClass("+++ b/src/index.ts")).toBe("cm-diff-file");
    expect(diffLineClass("--- /dev/null")).toBe("cm-diff-file");
    expect(diffLineClass("+++ /dev/null")).toBe("cm-diff-file");
  });

  it("correctly classifies single character diffs", () => {
    expect(diffLineClass("+")).toBe("cm-diff-add");
    expect(diffLineClass("-")).toBe("cm-diff-del");
  });

  it("handles empty or whitespace lines", () => {
    expect(diffLineClass("")).toBeNull();
    expect(diffLineClass("   ")).toBeNull();
    expect(diffLineClass(" normal code line")).toBeNull();
  });
});
