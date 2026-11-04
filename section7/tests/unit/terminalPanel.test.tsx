// Part 12 item 009: the terminal panel is on the run page, and its scrolling output can be reached with the keyboard.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TerminalPanel } from "../../src/components/TerminalPanel";

describe("TerminalPanel", () => {
  it("says plainly that nothing has streamed yet, and renders no output box", () => {
    const html = renderToStaticMarkup(<TerminalPanel results={[]} />);
    expect(html).toContain("Terminal &amp; test results");
    expect(html).toContain("Nothing has run yet");
    expect(html).not.toContain("<pre");
  });

  // M-A2: the panel used to print "No live terminal feed yet" from a
  // permanently-empty `results` prop while streaming transcript lines directly
  // underneath it.
  it("never claims there is no feed while it is showing transcript lines", () => {
    const html = renderToStaticMarkup(<TerminalPanel results={[]} lines={[" collecting …\n"]} />);
    expect(html).not.toContain("Nothing has run yet");
    expect(html).toContain("1 line");
    expect(html).toContain("<pre");
  });

  it("makes the scrolling output a labelled, focusable region so a keyboard can scroll it", () => {
    const html = renderToStaticMarkup(<TerminalPanel results={[]} lines={["npm test\n", "ok\n"]} />);
    const pre = html.match(/<pre[^>]*>/)?.[0] ?? "";
    expect(pre).toContain('tabindex="0"');
    expect(pre).toContain('role="region"');
    expect(pre).toContain('aria-label="Terminal output"');
    expect(html).toContain("npm test");
  });

  it("is mounted by the working area that the run page renders", () => {
    const src = readFileSync(join(process.cwd(), "src", "components", "WorkingArea.tsx"), "utf8");
    expect(src).toContain('import { TerminalPanel } from "./TerminalPanel"');
    expect(src).toMatch(/<TerminalPanel\s+results=\{\[\]\}\s+lines=\{derived\.terminalLines\}/);
    const page = readFileSync(join(process.cwd(), "app", "projects", "[projectId]", "runs", "[runId]", "page.tsx"), "utf8");
    expect(page).toContain("WorkingArea");
  });

  // H1: a result carries structured diagnostics, so the panel can show the
  // position the tool reported instead of echoing an opaque line.
  const result = {
    status: "failed" as const,
    exit_code: 1,
    passed_count: 0,
    failed_count: 1,
    skipped_count: 0,
    duration_ms: 120,
    summary: "Command 'hermesc check app.hms' failed with exit code 1.",
    diagnostics: [
      { path: "app.hms", line: 3, col: 7, kind: "error" as const, message: "expected ')'" },
      { kind: "warning" as const, message: "unused variable" },
    ],
  };

  it("shows the reported file:line:col and the kind of each diagnostic", () => {
    const html = renderToStaticMarkup(
      <TerminalPanel results={[{ ...result, diagnostics_source: "protocol" }]} />,
    );
    expect(html).toContain("app.hms:3:7");
    expect(html).toContain("expected &#x27;)&#x27;");
    expect(html).toContain("unused variable");
    // a record with no position renders no invented one
    expect(html).not.toContain("undefined");
    expect(html).not.toContain("null");
  });

  it("says when positions came from scanning rather than from the tool", () => {
    const scanned = renderToStaticMarkup(
      <TerminalPanel results={[{ ...result, diagnostics_source: "scan" }]} />,
    );
    expect(scanned).toContain("This tool reports no file or line");

    const protocol = renderToStaticMarkup(
      <TerminalPanel results={[{ ...result, diagnostics_source: "protocol" }]} />,
    );
    expect(protocol).not.toContain("This tool reports no file or line");
  });

  it("admits when the list was truncated instead of silently dropping the rest", () => {
    const html = renderToStaticMarkup(
      <TerminalPanel results={[{ ...result, diagnostics_truncated: true }]} />,
    );
    expect(html).toContain("Showing the first 20.");
  });
});
