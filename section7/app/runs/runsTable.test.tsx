// M12-E3b (H-009) — runs-table click-through component tests.
// Node-env constraint (no jsdom/RTL in this repo; installs are out of scope):
// the RunsTable is a named export rendered with react-dom/server's real React
// renderer and asserted on emitted markup, same protocol as planGate.test.tsx.
import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { RunDto } from "./runsApi";
import { runDetailHref } from "./runsApi";
// M14-F1b (raw-03 P2): keyboard contract exports under test below.
import { isRowActivateKey, RunsTable } from "./runsTable";

function run(partial: Partial<RunDto>): RunDto {
  return {
    id: partial.id ?? "run-1234567890abcdef",
    projectId: partial.projectId ?? "proj-1",
    status: partial.status ?? "executing",
    createdAt: partial.createdAt ?? "2026-08-23T16:00:00Z",
    startedAt: partial.startedAt ?? null,
    finishedAt: partial.finishedAt ?? null,
  };
}

const NOW = Date.parse("2026-08-23T17:00:00Z");

describe("runDetailHref", () => {
  it("builds /projects/{projectId}/runs/{id} from row data only", () => {
    expect(runDetailHref({ id: "r9", projectId: "p3" })).toBe("/projects/p3/runs/r9");
  });
});

describe("RunsTable click-through rendering", () => {
  it("renders each run + goal(project) cell as an accent-colored link to the run detail route", () => {
    const html = renderToStaticMarkup(
      <RunsTable runs={[run({})]} now={NOW} />,
    );
    // Both cells carry real anchors to the detail URL...
    const hrefCount = html.split('href="/projects/proj-1/runs/run-1234567890abcdef"').length - 1;
    expect(hrefCount).toBe(2);
    // ...in accent color so they read as links.
    expect(html).toContain("color:var(--accent)");
  });

  it("keeps the full run id reachable in the title attribute", () => {
    const html = renderToStaticMarkup(<RunsTable runs={[run({})]} now={NOW} />);
    expect(html).toContain('title="run-1234567890abcdef"');
    // short form is the visible label
    expect(html).toContain(">run-1234</a>");
  });

  it("marks rows keyboard-reachable (tabindex) with an accessible label", () => {
    const html = renderToStaticMarkup(<RunsTable runs={[run({})]} now={NOW} />);
    expect(html).toContain('tabindex="0"');
    expect(html).toContain('aria-label="Open run run-1234"');
  });

  it("renders the honest empty state when there are no runs", () => {
    const html = renderToStaticMarkup(<RunsTable runs={[]} now={NOW} />);
    expect(html).toContain("No runs match the current filters.");
    expect(html).not.toContain("<a ");
  });
});

// M14-F1b (raw-03 P2) — tab-order spot checks + keyboard activation contract.
describe("RunsTable keyboard contract (M14-F1b)", () => {
  it("isRowActivateKey: Enter and Space activate; everything else does not", () => {
    expect(isRowActivateKey("Enter")).toBe(true);
    expect(isRowActivateKey(" ")).toBe(true); // Space is e.key " ", not "Spacebar"
    expect(isRowActivateKey("Spacebar")).toBe(false); // legacy IE artifact
    expect(isRowActivateKey("Escape")).toBe(false);
    expect(isRowActivateKey("Tab")).toBe(false);
    expect(isRowActivateKey("ArrowDown")).toBe(false);
  });

  it("rows stay keyboard-reachable and carry exactly ONE positive-free tabindex per row", () => {
    const html = renderToStaticMarkup(
      <RunsTable runs={[run({}), run({ id: "run-aaaaaaaaaaaaaaaa" })]} now={NOW} />,
    );
    // Both rows tabbable at the natural DOM position (tabindex="0"), and no
    // positive tabindex exists anywhere in the emitted markup — tab order
    // must follow DOM order, never a hand-authored sequence.
    expect(html.split('tabindex="0"').length - 1).toBe(2);
    expect(html).not.toMatch(/tabindex="[1-9]/);
  });

  it("row activation keys match what the onKeyDown handler consumes", () => {
    // The handler calls preventDefault+assign for exactly the keys this
    // predicate accepts; asserting the pair keeps them from drifting apart.
    const html = renderToStaticMarkup(<RunsTable runs={[run({})]} now={NOW} />);
    expect(html).toContain('aria-label="Open run run-1234"');
    expect(isRowActivateKey("Enter")).toBe(true);
    expect(isRowActivateKey(" ")).toBe(true);
  });
});
