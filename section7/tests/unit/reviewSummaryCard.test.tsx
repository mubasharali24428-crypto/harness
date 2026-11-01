// W24: the review summary on the run page says what it does not know and never prints a $0 cost.
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ReviewSummaryCard, readReviewSummary } from "../../app/projects/[projectId]/runs/[runId]/reviewSummaryCard";

const known = {
  runId: "r1",
  summary: {
    schema: 1,
    basis: "recorded from the agent's own tool calls and token usage; the harness re-ran nothing",
    files: [{ path: "calc.py", change: "modified", added: 1, removed: 1 }],
    tests: { known: true, runs: [{ command: "pytest -q", result: "passed" }], note: "exit status only" },
    failures: [],
    cost: { usd: 0.0015, source: "cost ledger", model: "anthropic/claude-haiku-5.5", inputTokens: 1500, outputTokens: 150 },
    elapsedSeconds: 42.5,
    uncertainty: [],
  },
};

const render = (raw: unknown) => renderToStaticMarkup(<ReviewSummaryCard summary={readReviewSummary(raw)!} />);

describe("ReviewSummaryCard", () => {
  it("lists files, tests, cost and time from the recorded facts", () => {
    const html = render(known);
    expect(html).toContain("calc.py · modified · +1 −1");
    expect(html).toContain("<code>pytest -q</code> · passed");
    expect(html).toContain("Failures");
    expect(html).toContain("none recorded");
    expect(html).toContain("$0.001500 · 1,500 in / 150 out · anthropic/claude-haiku-5.5 · cost ledger");
    expect(html).toContain("42.5 s");
    expect(html).not.toContain("Still unknown");
  });

  it("writes unknowns as unknown: no tests, no cost, no time", () => {
    const html = render({
      summary: {
        ...known.summary,
        tests: { known: false, runs: [] },
        cost: { usd: null, source: null, model: null, inputTokens: null, outputTokens: null },
        elapsedSeconds: null,
        failures: [{ kind: "test", what: "pytest -q failed" }],
        uncertainty: [{ code: "USAGE_UNAVAILABLE", text: "no token usage was recorded, so the cost is unknown" }],
      },
    });
    expect(html).toContain("unknown (no tool calls were recorded for this run)");
    expect(html).toContain("Cost</dt><dd>unknown</dd>");
    expect(html).toContain("Time</dt><dd>unknown</dd>");
    expect(html).not.toContain("$0");
    expect(html).toContain("pytest -q failed");
    expect(html).toContain("Still unknown");
    expect(html).toContain("no token usage was recorded");
  });

  it("renders nothing for a response it does not understand", () => {
    for (const raw of [null, {}, { summary: { schema: 2 } }, { summary: { ...known.summary, tests: {} } }, "x"]) {
      expect(readReviewSummary(raw)).toBeNull();
    }
  });
});
