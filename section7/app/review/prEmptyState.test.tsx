// M12-E4b (H-001) — the honest empty state must carry the exact mission copy,
// and nothing that smells like hand-edited localStorage ("set harness.prId").
// Node-env constraint: rendered with react-dom/server, same as
// app/dashboard/planGate.test.tsx.
import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { PrEmptyState, PR_PENDING_COPY } from "./prEmptyState";

describe("PrEmptyState", () => {
  it("renders the exact honest pending copy", () => {
    const html = renderToStaticMarkup(<PrEmptyState />);
    expect(html).toContain(PR_PENDING_COPY);
    expect(html).toContain(
      "No pull request yet — it appears here the moment the agent opens one",
    );
  });

  it("contains no dead-end localStorage instruction", () => {
    const html = renderToStaticMarkup(<PrEmptyState />);
    expect(html.toLowerCase()).not.toContain("harness.prid");
    expect(html.toLowerCase()).not.toContain("localstorage");
  });

  it("renders inside a card like the rest of the area", () => {
    const html = renderToStaticMarkup(<PrEmptyState />);
    expect(html).toContain('class="card"');
  });
});
