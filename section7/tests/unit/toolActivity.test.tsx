import { readFileSync } from "node:fs";
import { join } from "node:path";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ToolActivity } from "../../src/components/ToolActivity";
import type { ToolCallView } from "../../src/lib/deriveView";

const call = (over: Partial<ToolCallView> = {}): ToolCallView => ({
  callId: "c1",
  name: "bash",
  arguments: '{"command":"ls"}',
  ok: null,
  error: null,
  output: "",
  durationMs: null,
  ...over,
});

describe("ToolActivity", () => {
  it("says what it will show instead of pretending a run has activity", () => {
    const html = renderToStaticMarkup(<ToolActivity calls={[]} />);
    expect(html).toContain("No tool calls yet");
    expect(html).not.toContain("<ol");
  });

  it("shows name, input and outcome with the duration the events carried", () => {
    const html = renderToStaticMarkup(
      <ToolActivity calls={[call({ ok: true, output: "a.txt", durationMs: 450 })]} />,
    );
    expect(html).toContain("bash");
    expect(html).toContain("ok");
    expect(html).toContain("450ms");
    expect(html).toContain("a.txt");
    expect(html).toContain("ls");
  });

  it("reports a call without a result as running, never as success", () => {
    const html = renderToStaticMarkup(<ToolActivity calls={[call()]} />);
    expect(html).toContain("running");
    expect(html).not.toContain(">ok<");
  });

  it("shows the error a failed result carried", () => {
    const html = renderToStaticMarkup(
      <ToolActivity calls={[call({ ok: false, error: "outside-workspace" })]} />,
    );
    expect(html).toContain("error");
    expect(html).toContain("outside-workspace");
  });

  it("is mounted on the run page next to the terminal panel", () => {
    const src = readFileSync(join(process.cwd(), "src", "components", "WorkingArea.tsx"), "utf8");
    expect(src).toContain('import { ToolActivity } from "./ToolActivity"');
    expect(src).toContain("<ToolActivity calls={derived.toolCalls} />");
  });
});
