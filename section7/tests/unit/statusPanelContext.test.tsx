import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StatusPanel } from "../../src/components/StatusPanel";
import type { WorkingAreaDerivedView } from "../../src/lib/deriveView";

const derived = (over: Partial<WorkingAreaDerivedView> = {}): WorkingAreaDerivedView => ({
  blockerQuestion: null,
  lastReview: null,
  terminalLines: [],
  changedPaths: [],
  toolCalls: [],
  contextWindow: null,
  compactedTokens: 0,
  ...over,
});

describe("StatusPanel — context line", () => {
  it("says nothing about context when nothing reported it", () => {
    const html = renderToStaticMarkup(
      <StatusPanel status={null} derived={derived()} connectionState="open" />,
    );
    expect(html).not.toContain("Context:");
  });

  it("states the window and that no compaction has happened", () => {
    const html = renderToStaticMarkup(
      <StatusPanel
        status={null}
        derived={derived({ contextWindow: 1_000_000 })}
        connectionState="open"
      />,
    );
    expect(html).toContain("1M window");
    expect(html).toContain("no compaction yet");
  });

  it("reports compacted tokens when the runtime compacted", () => {
    const html = renderToStaticMarkup(
      <StatusPanel
        status={null}
        derived={derived({ contextWindow: 200_000, compactedTokens: 12_500 })}
        connectionState="open"
      />,
    );
    expect(html).toContain("200k window");
    expect(html).toContain("12,500 tokens compacted");
  });
});
