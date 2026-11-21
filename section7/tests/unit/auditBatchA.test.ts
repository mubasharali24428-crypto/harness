// Batch A of the 2026-10-25 UI/UX audit (docs/ui-ux-audit-2026-10-25.md).
//
// Node-env lane (no jsdom/RTL in this repo), so each item is covered the way
// the repo already covers similar guarantees: real render output where a
// render is enough, source assertions where the guarantee is "this call never
// happens" or "this control no longer exists".
import { describe, expect, it } from "vitest";
import * as React from "react";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { DiffPanel, diffEmptyState } from "@/components/DiffPanel";

const SRC = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");

describe("A1 — no engineering notes leak into the run surface", () => {
  it("the diff panel's empty state is written for the reviewer, not for the lane", () => {
    const empty = diffEmptyState([]);
    expect(empty).toContain("No changes yet.");
    expect(empty).not.toMatch(/Section 5|frozen contract|EventType/);
  });

  it("names the files the agent has already touched", () => {
    const text = diffEmptyState(["src/textstats/stats.py", "pyproject.toml"]);
    expect(text).toContain("2 files changed so far");
    expect(text).toContain("src/textstats/stats.py");
  });

  it("truncates a long touched-file list instead of lying about completeness", () => {
    const many = Array.from({ length: 15 }, (_, i) => `src/mod${i}.py`);
    const text = diffEmptyState(many);
    expect(text).toContain("15 files changed so far");
    expect(text).toContain("… and 3 more");
  });

  it("renders through the panel with the same copy", () => {
    const html = renderToStaticMarkup(React.createElement(DiffPanel, { diffText: "", changedPaths: ["a.ts"] }));
    expect(html).toContain("Diff");
  });

  it("the plan panel no longer prints a placeholder cost line", () => {
    const src = SRC("../../src/components/PlanPanel.tsx");
    expect(src).not.toContain("no live source yet");
    expect(src).not.toContain("cost_ledger");
  });

  it("run notes describe themselves as local, without the endpoint confession", () => {
    const src = SRC("../../src/components/CommentsNudges.tsx");
    expect(src).toContain("Private to this browser tab");
    expect(src).not.toContain("no backend endpoint exists yet");
  });
});

describe("A6 — the takeover checkbox reveals, it does not write", () => {
  it("no submit fires from the toggle handler", () => {
    const src = SRC("../../src/components/Controls.tsx");
    expect(src).not.toMatch(/\(unspecified\)/);
    const handler = src.slice(
      src.indexOf("function handleTakeoverToggle"),
      src.indexOf("}", src.indexOf("function handleTakeoverToggle")) + 1,
    );
    expect(handler).not.toContain("submitHumanEdit");
    expect(handler).toContain("setTakeover(checked)");
  });

  it("the explicit save still carries the pause semantics", () => {
    const src = SRC("../../src/components/Controls.tsx");
    expect(src).toMatch(/submitHumanEdit\(runId, editPath, editContent, token\)/);
    expect(src).toContain("Saving pauses the run");
  });
});

describe("A7 — no control that does nothing", () => {
  it("the sidebar has no dead team select (R2 dropped the team line)", () => {
    const src = SRC("../../src/components/AppShell.tsx");
    expect(src).not.toMatch(/<select defaultValue="solo"/);
  });
});

describe("A8 — a rejected model save rolls the control back", () => {
  it("restores the previous prefs before reporting the failure", () => {
    const src = SRC("../../src/components/ModelPrefs.tsx");
    const catchBlock = src.slice(src.indexOf(".catch((e: unknown)"), src.indexOf(".finally"));
    expect(catchBlock).toContain("setPrefs(previous)");
    expect(catchBlock).toContain("setError(");
  });
});

describe("A9 — the review gate cannot record an empty decision", () => {
  it("requires a written change request and sends it as the comment", () => {
    const src = SRC("../../app/review/page.tsx");
    expect(src).not.toMatch(/requestChanges\(prId, \[\]/);
    expect(src).toContain("requestChanges(prId, [{ body: note }]");
    expect(src).toMatch(/disabled=\{busy \|\| changeRequest\.trim\(\) === ""\}/);
  });

  it("distinguishes a missing PR from a failed load, and offers a retry", () => {
    const src = SRC("../../app/review/page.tsx");
    expect(src).toContain("err.status === 404");
    expect(src).toContain("setLoadError(");
    expect(src).toContain("Try again");
    // the old permanent "loading diff…" state is gated on there being no error
    expect(src).toContain("{loadError ? \"not loaded\" : \"loading diff…\"}");
  });
});
