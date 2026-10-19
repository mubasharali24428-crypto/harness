import * as React from "react";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SessionSixWorkbench } from "./sessionSixWorkbench";

describe("SessionSixWorkbench", () => {
  it("renders labeled controls and keyboard-operable native actions", () => {
    const html = renderToStaticMarkup(<SessionSixWorkbench runId="run-1" token="token" />);
    for (const label of [
      "Task statement", "Plan versions", "Subtasks and execution", "Artifacts and changes",
      "Search and export", "Reviewer comment", "Upload an artifact", "Request deletion of your data",
    ]) expect(html).toContain(label);
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain('aria-live="assertive"');
    expect(html).toContain("Refresh work history");
    expect(html).toContain("Create proposed version");
  });
});
