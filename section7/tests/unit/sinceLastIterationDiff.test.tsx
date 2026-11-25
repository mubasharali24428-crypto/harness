// R4: the since-last view renders the newest checkpoint's hunks, names where
// they came from, and only enables revert once a hunk is ticked.
import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { CheckpointFile } from "@/lib/api";
import {
  SinceLastIterationDiff,
  type SinceLastIterationDiffProps,
} from "@/components/workspace/SinceLastIterationDiff";

const files: CheckpointFile[] = [
  {
    path: "src/calc.py",
    binary: false,
    hunks: [{ index: 0, header: "@@ -1,2 +1,2 @@", lines: [" a", "-old", "+new"], additions: 1, deletions: 1 }],
  },
];

const base: SinceLastIterationDiffProps = {
  pick: {
    checkpoint: { sha: "abcdef1234567890", message: "checkpoint: post-edit step-2", createdAt: "2026-11-25T10:00:00Z", taskId: "run-1" },
    linked: true,
  },
  files,
  error: null,
  selected: new Set<string>(),
  busy: false,
  notice: null,
  onToggle: () => undefined,
  onRevert: () => undefined,
  onRetry: () => undefined,
};

const render = (o: Partial<SinceLastIterationDiffProps> = {}) =>
  renderToStaticMarkup(<SinceLastIterationDiff {...base} {...o} />);

describe("SinceLastIterationDiff", () => {
  it("shows the checkpoint's hunks with add/del tones and its origin", () => {
    const html = render();
    expect(html).toContain('data-testid="since-last-diff"');
    expect(html).toContain("this run’s newest snapshot");
    expect(html).toContain("abcdef1234");
    expect(html).toContain("1 file · 1 hunk · +1 −1");
    expect(html).toContain('class="ws-diff-line add"');
    expect(html).toContain('class="ws-diff-line del"');
    expect(html).toContain("Revert hunk 1 in src/calc.py");
  });

  it("keeps revert disabled until a hunk is selected", () => {
    expect(render()).toMatch(/<button[^>]*disabled=""[^>]*data-testid="since-last-revert"/);
    const html = render({ selected: new Set(["src/calc.py:0"]) });
    expect(html).toContain("Revert 1 selected hunk");
    expect(html).not.toMatch(/<button[^>]*disabled=""[^>]*data-testid="since-last-revert"/);
  });

  it("says when the snapshot is not tagged with the run", () => {
    expect(render({ pick: { ...base.pick, linked: false } })).toContain("not tagged with this run");
  });

  it("explains an empty iteration and a load failure", () => {
    expect(render({ files: [] })).toContain("No textual changes in the last iteration");
    expect(render({ error: "sandbox down" })).toContain("The last iteration didn’t load");
  });
});
