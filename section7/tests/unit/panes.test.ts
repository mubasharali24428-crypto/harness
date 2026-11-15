import { describe, expect, it } from "vitest";
import { paneFromSearch, searchWithPane } from "@/components/workspace/panes";

describe("right pane in the URL", () => {
  it("reads ?pane= and ignores unknown values", () => {
    expect(paneFromSearch("?pane=diff")).toBe("diff");
    expect(paneFromSearch("?pane=nope")).toBeNull();
    expect(paneFromSearch("")).toBeNull();
  });
  it("opens the matching pane for each old tab link", () => {
    expect(paneFromSearch("?tab=changes&demo=1")).toBe("diff");
    expect(paneFromSearch("?tab=plan")).toBe("plan");
    expect(paneFromSearch("?tab=working")).toBe("terminal");
    expect(paneFromSearch("?tab=team")).toBe("advanced");
    expect(paneFromSearch("?tab=overview")).toBeNull();
  });
  it("writes the pane, drops the old tab and keeps other params", () => {
    expect(searchWithPane("?tab=changes&demo=1", "plan")).toBe("?demo=1&pane=plan");
    expect(searchWithPane("?pane=diff", null)).toBe("");
  });
});
