// W2 (N04) — the affordance has to exist, be reachable only where it applies,
// and never look actionable while it would do nothing. The two-call sequence
// behind it is pinned in resumeWithChanges.test.ts; this file pins visibility,
// labelling and the empty-state guard.

import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Controls } from "../../src/components/Controls";
import type { RunStatus } from "@contracts/api";

function render(state: RunStatus["state"]) {
  return renderToStaticMarkup(
    <Controls
      runId="r1"
      token="tok"
      status={{ state } as RunStatus}
      onDiffChange={() => {}}
      onActionComplete={() => {}}
    />,
  );
}

describe("Controls — resume with changes", () => {
  it("offers the affordance while a run is stuck", () => {
    const html = render("blocked");
    expect(html).toContain("Resume with changes");
    expect(html).toContain('aria-label="What should change before resuming"');
  });

  it("offers it on a paused run too", () => {
    expect(render("paused")).toContain("Resume with changes");
  });

  it("does not offer it to a run that is already executing", () => {
    const html = render("executing");
    expect(html).not.toContain("Resume with changes");
  });

  it("does not offer it once the run is done", () => {
    expect(render("done")).not.toContain("Resume with changes");
  });

  it("starts disabled, because resuming with no change is just Resume", () => {
    const html = render("blocked");
    const button = html.slice(html.indexOf("Resume with changes"));
    expect(button).toContain("disabled");
  });

  it("states what it will do, rather than leaving the operator to guess", () => {
    // The correction is durable guidance the agent reads when it resumes — not a
    // chat message, and not a restart from scratch.
    expect(render("blocked")).toContain("before the agent continues");
  });
});
