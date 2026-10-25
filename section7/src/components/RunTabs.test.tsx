import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { RUN_TABS, RunTabBar, RunTabPanel, nextTabIndex, tabFromSearch } from "./RunTabs";

describe("tab selection from the URL", () => {
  it("accepts a known tab and falls back to overview", () => {
    expect(tabFromSearch("?tab=working")).toBe("working");
    expect(tabFromSearch("?tab=changes&x=1")).toBe("changes");
    expect(tabFromSearch("?tab=../../etc")).toBe("overview");
    expect(tabFromSearch("")).toBe("overview");
  });
});

describe("keyboard model", () => {
  it("wraps with arrows and jumps with Home/End", () => {
    const n = RUN_TABS.length;
    expect(nextTabIndex(0, "ArrowLeft", n)).toBe(n - 1);
    expect(nextTabIndex(n - 1, "ArrowRight", n)).toBe(0);
    expect(nextTabIndex(2, "Home", n)).toBe(0);
    expect(nextTabIndex(2, "End", n)).toBe(n - 1);
    expect(nextTabIndex(2, "Enter", n)).toBe(2);
  });
});

describe("rendered tabs", () => {
  it("exposes the ARIA tabs contract and marks one selection", () => {
    const html = renderToStaticMarkup(<RunTabBar active="plan" onChange={() => undefined} />);
    expect(html).toContain('role="tablist"');
    expect(html.match(/role="tab"/g)?.length).toBe(RUN_TABS.length);
    expect(html.match(/aria-selected="true"/g)?.length).toBe(1);
    expect(html).toContain('aria-controls="run-panel-working"');
  });

  it("unmounts inactive panels unless asked to keep them", () => {
    expect(
      renderToStaticMarkup(
        <RunTabPanel id="team" active={false}>
          <span>team</span>
        </RunTabPanel>,
      ),
    ).toBe("");
    const kept = renderToStaticMarkup(
      <RunTabPanel id="team" active={false} keepMounted>
        <span>team</span>
      </RunTabPanel>,
    );
    expect(kept).toContain("hidden");
    expect(kept).toContain("team");
  });
});
