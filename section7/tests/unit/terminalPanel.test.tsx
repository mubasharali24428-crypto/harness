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
    expect(html).toContain("No live terminal feed yet");
    expect(html).not.toContain("<pre");
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
});
