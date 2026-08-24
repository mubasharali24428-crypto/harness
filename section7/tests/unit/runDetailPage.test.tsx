// M14-W4 F3b (W4-C2) — run detail page render-per-field-presence matrix.
// Node-env constraint (no jsdom/RTL in this repo; installs are out of scope):
// the presentational RunDetailAggregate is rendered with react-dom/server's
// real React renderer and asserted on emitted markup, same protocol as
// runsTable.test.tsx / planGate.test.tsx. The matrix proves honest
// degradation per missing field: each section exists ONLY when its data
// exists, and null never masquerades as a fake zero or an invented link.
import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { RunAggregateDto } from "../../app/runs/runsApi";
import { RUN_AGGREGATE_STEPS_MAX } from "../../app/runs/runsApi";
// W4 gate repair: RunDetailAggregate lives in its own module now (the page
// module keeps only Next's default export).
import { RunDetailAggregate } from "../../app/projects/[projectId]/runs/[runId]/runDetailAggregate";

/** Full aggregate with every W4-C2 field populated. */
function fullAgg(partial: Partial<RunAggregateDto> = {}): RunAggregateDto {
  return {
    runId: partial.runId ?? "run-1234567890abcdef",
    state: partial.state ?? "pr_opened",
    planId: partial.planId ?? "plan-1",
    planApproved: partial.planApproved ?? true,
    isHumanEditing: partial.isHumanEditing ?? false,
    executing: partial.executing ?? false,
    engineMode: partial.engineMode ?? "live",
    costUsd: partial.costUsd !== undefined ? partial.costUsd : 12.5,
    prId:
      partial.prId !== undefined ? partial.prId : "pr-777",
    url:
      partial.url !== undefined
        ? partial.url
        : "https://github.com/acme/repo/pull/42",
    steps:
      partial.steps !== undefined
        ? partial.steps
        : [
            { seq: 1, type: "plan_proposed", ts: "2026-08-23T10:00:00Z" },
            { seq: 2, type: "plan_approved", ts: "2026-08-23T10:01:00Z" },
            { seq: 3, type: "execution_started", ts: null },
          ],
  };
}

const NOW = Date.parse("2026-08-23T17:00:00Z");

function htmlFor(agg: RunAggregateDto): string {
  return renderToStaticMarkup(<RunDetailAggregate agg={agg} now={NOW} />);
}

describe("RunDetailAggregate — all fields present", () => {
  const html = htmlFor(fullAgg());

  it("renders the state badge with the runs-index pill vocabulary", () => {
    expect(html).toContain('class="pill blue"');
    expect(html).toContain(">pr_opened</span>");
  });

  it("renders the NUMERIC-exact cost line", () => {
    expect(html).toContain("$12.500000");
  });

  it("renders the PR card with a real external anchor", () => {
    expect(html).toContain('href="https://github.com/acme/repo/pull/42"');
    expect(html).toContain("View pull request →");
    expect(html).toContain("pr-777");
  });

  it("renders the step timeline newest-first with human labels", () => {
    const execStart = html.indexOf("Execution started");
    const proposed = html.indexOf("Plan proposed");
    expect(execStart).toBeGreaterThanOrEqual(0);
    expect(proposed).toBeGreaterThan(execStart);
    // raw wire type stays reachable in the title attr of unknown-ish rows
    expect(html).toContain('title="—">'); // null ts renders as — via formatter
  });
});

describe("RunDetailAggregate — degradation per missing field", () => {
  it("costUsd null → NO cost line at all, never a fabricated $0", () => {
    const html = htmlFor(fullAgg({ costUsd: null }));
    expect(html).not.toContain("$");
    expect(html).not.toContain("Cost:");
  });

  it("costUsd 0 (server-sent genuine zero) → honest $0.000000", () => {
    const html = htmlFor(fullAgg({ costUsd: 0 }));
    expect(html).toContain("$0.000000");
  });

  it("prId+url both null → no PR card section", () => {
    const html = htmlFor(fullAgg({ prId: null, url: null }));
    expect(html).not.toContain("Pull request");
    expect(html).not.toContain("<a ");
  });

  it("url null but prId set → identity-only PR card, no fake link", () => {
    const html = htmlFor(fullAgg({ url: null }));
    expect(html).toContain("Pull request");
    expect(html).toContain("No URL yet");
    expect(html).not.toContain("<a href=");
  });

  it("url set but prId null → still renders the link card (drift-tolerant)", () => {
    const html = htmlFor(fullAgg({ prId: null }));
    expect(html).toContain("Pull request");
    expect(html).toContain('href="https://github.com/acme/repo/pull/42"');
  });

  it("steps empty → explicit empty state, never filler rows", () => {
    const html = htmlFor(fullAgg({ steps: [] }));
    expect(html).toContain("No events recorded for this run yet.");
    expect(html).not.toContain("Execution started");
  });

  it("step ts null → timestamp degrades to — without crashing", () => {
    const html = htmlFor(
      fullAgg({
        steps: [{ seq: 9, type: "engine_completed", ts: null }],
      }),
    );
    expect(html).toContain("Engine completed");
    expect(html).toContain("title=\"—\"");
  });

  it("unknown event type humanizes instead of dropping", () => {
    const html = htmlFor(
      fullAgg({ steps: [{ seq: 1, type: "mystery_event", ts: null }] }),
    );
    expect(html).toContain("mystery event");
  });

  it("every enriched field null → page is just header + empty timeline", () => {
    const html = htmlFor(
      fullAgg({ costUsd: null, prId: null, url: null, steps: [] }),
    );
    expect(html).not.toContain("$");
    expect(html).not.toContain("Pull request");
    expect(html).toContain("No events recorded for this run yet.");
    // The state badge ALWAYS renders — that's the one field never optional.
    expect(html).toContain(">pr_opened</span>");
  });
});

describe("RunDetailAggregate — truncation honesty + cap", () => {
  it("labels the timeline as latest-N-of-M when over the cap", () => {
    const steps = Array.from({ length: 130 }, (_, i) => ({
      seq: i,
      type: "terminal_output",
      ts: null,
    }));
    const html = htmlFor(fullAgg({ steps }));
    expect(steps.length).toBeGreaterThan(RUN_AGGREGATE_STEPS_MAX);
    expect(html).toContain(`(${RUN_AGGREGATE_STEPS_MAX} latest of 130)`);
  });

  it("renders at most 100 rows even if handed more", () => {
    const steps = Array.from({ length: 150 }, (_, i) => ({
      seq: i,
      type: "terminal_output",
      ts: null,
    }));
    const html = htmlFor(fullAgg({ steps }));
    const liCount = html.split("<li").length - 1;
    expect(liCount).toBe(RUN_AGGREGATE_STEPS_MAX);
  });

  it("shows plain counts when nothing was truncated", () => {
    const html = htmlFor(fullAgg());
    expect(html).toContain("(3 latest of 3)");
  });
});
