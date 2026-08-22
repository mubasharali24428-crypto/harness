import { describe, it, expect } from "vitest";
import { diffLineClass } from "@/lib/codemirror";

describe("diffLineClass", () => {
  it("classifies added lines", () => {
    expect(diffLineClass("+def foo(): pass")).toBe("cm-diff-add");
  });

  it("classifies removed lines", () => {
    expect(diffLineClass("-def foo(): pass")).toBe("cm-diff-del");
  });

  it("classifies hunk headers", () => {
    expect(diffLineClass("@@ -1,1 +1,2 @@")).toBe("cm-diff-hunk");
  });

  it("classifies file headers before the +/- check", () => {
    expect(diffLineClass("+++ b/auth.py")).toBe("cm-diff-file");
    expect(diffLineClass("--- a/auth.py")).toBe("cm-diff-file");
  });

  it("returns null for unrelated lines", () => {
    expect(diffLineClass("def foo(): pass")).toBeNull();
  });
});
