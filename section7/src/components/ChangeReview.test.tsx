import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import type { CheckpointDto, CheckpointFile } from "@/lib/api";
import { ChangeReviewView, type ChangeReviewViewProps } from "./ChangeReview";

const snapshots: CheckpointDto[] = [
  { sha: "aaaa111122223333", message: "checkpoint: post-edit step-2", createdAt: "2026-10-25T10:00:00Z", taskId: "t1" },
  { sha: "bbbb444455556666", message: "checkpoint: pre-edit", createdAt: "2026-10-25T09:59:00Z", taskId: "t1" },
];

const files: CheckpointFile[] = [
  {
    path: "src/calc.py",
    binary: false,
    hunks: [
      { index: 0, header: "@@ -1,2 +1,2 @@", lines: [" a", "-old", "+new"], additions: 1, deletions: 1 },
      { index: 1, header: "@@ -9,2 +9,2 @@", lines: [" x", "-gone", "+kept"], additions: 1, deletions: 1 },
    ],
  },
  { path: "logo.png", binary: true, hunks: [] },
];

const base: ChangeReviewViewProps = {
  snapshots,
  sha: snapshots[0].sha,
  files,
  selected: new Set<string>(),
  busy: null,
  notice: null,
  error: null,
  confirmRestore: false,
  onSelectSha: () => undefined,
  onToggle: () => undefined,
  onRevert: () => undefined,
  onRequestRestore: () => undefined,
  onCancelRestore: () => undefined,
  onConfirmRestore: () => undefined,
  onRetry: () => undefined,
};

const render = (overrides: Partial<ChangeReviewViewProps> = {}) =>
  renderToStaticMarkup(<ChangeReviewView {...base} {...overrides} />);

describe("changes view", () => {
  it("explains an empty history instead of rendering a blank panel", () => {
    const html = render({ snapshots: [], files: [] });
    expect(html).toContain("No snapshots yet");
    expect(html).toContain("Snapshots appear after the agent records its first edit batch");
  });

  it("renders one reviewable group per hunk with the summary line", () => {
    const html = render();
    expect(html).toContain("2 files · 2 hunks · +2 −2 · 1 binary");
    expect(html.match(/Revert this hunk/g)?.length).toBe(2);
    expect(html).toContain('aria-label="Revert hunk 2 in src/calc.py"');
    expect(html).toContain("Binary content — hunk-level review is not available");
    expect(html).toContain("Revert 0 selected hunks");
  });

  it("disables revert until a hunk is selected, and counts the selection", () => {
    const off = render();
    expect(off).toContain('disabled=""');

    const on = render({ selected: new Set(["src/calc.py:1"]) });
    expect(on).toContain("Revert 1 selected hunk");
    expect(on).toContain("Restore this snapshot…");
  });

  it("shows the server's conflict text verbatim", () => {
    const html = render({
      notice: { tone: "danger", text: "the workspace changed since this snapshot" },
    });
    expect(html).toContain("the workspace changed since this snapshot");
    expect(html).toContain('role="alert"');
  });

  it("confirms a restore in a dialog with the target sha", () => {
    const html = render({ confirmRestore: true });
    expect(html).toContain('role="dialog"');
    expect(html).toContain("Restore this snapshot?");
    expect(html).toContain("aaaa11112222");
    expect(html).toContain("Restore snapshot");
  });
});
