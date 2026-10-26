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
  goalImages: null,
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

describe("StatusPanel — goal image line", () => {
  it("stays silent when no attempt reported images", () => {
    const html = renderToStaticMarkup(
      <StatusPanel status={null} derived={derived()} connectionState="open" />,
    );
    expect(html).not.toContain("with the goal");
  });

  it("counts attached images and names the ones that did not travel", () => {
    const html = renderToStaticMarkup(
      <StatusPanel
        status={null}
        derived={derived({
          goalImages: {
            attached: 2,
            skipped: [{ artifactId: "a3", name: "mock.png", reason: "image count limit" }],
            refused: null,
          },
        })}
        connectionState="open"
      />,
    );
    expect(html).toContain("2 images with the goal");
    expect(html).toContain("1 not sent");
    expect(html).toContain("mock.png: image count limit");
  });

  it("says plainly when nothing reached the model", () => {
    const html = renderToStaticMarkup(
      <StatusPanel
        status={null}
        derived={derived({
          goalImages: {
            attached: 0,
            skipped: [],
            refused: "model x is not in HARNESS_VISION_MODELS; goal images were not sent",
          },
        })}
        connectionState="open"
      />,
    );
    expect(html).toContain("no images reached the model");
    expect(html).toContain("not in HARNESS_VISION_MODELS");
  });
});
