import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ReviewPanelView } from "../../src/components/ReviewPanel";
import type { ChangeSet } from "../../src/lib/reviewModel";

const base: ChangeSet = {
  changeSetId: "cs1",
  sessionId: "s1",
  title: "Rename helper",
  status: "open",
  files: [
    {
      path: "src/app.py",
      baseSha256: "x",
      drift: "current",
      hunks: [
        {
          id: "h0",
          oldStart: 1,
          oldCount: 1,
          newStart: 1,
          newCount: 1,
          lines: [
            { kind: "del", text: "old" },
            { kind: "add", text: "new" },
          ],
        },
      ],
      decisions: { h0: { decision: "accept" } },
    },
  ],
};
const noop = () => {};
const render = (over: Partial<React.ComponentProps<typeof ReviewPanelView>> = {}) =>
  renderToStaticMarkup(
    <ReviewPanelView cs={base} canWrite busy={false} error={null} applied={null} focusedKey={null}
      onDecide={noop} onApply={noop} onCancel={noop} {...over} />,
  );

describe("ReviewPanelView", () => {
  it("renders accessible hunk groups, pressed state and diff line markers", () => {
    const html = render();
    expect(html).toContain('aria-label="Hunk 1 of 1 in src/app.py"');
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain("-old");
    expect(html).toContain("+new");
    expect(html).toContain('data-decision="accept"');
    expect(html).toContain("1 accepted, 0 rejected, 0 pending of 1 hunks");
  });
  it("read-only role disables every decision control and the apply button", () => {
    const html = render({ canWrite: false });
    expect(html).toContain("read-only");
    expect((html.match(/disabled=""/g) ?? []).length).toBeGreaterThanOrEqual(4);
    expect(html).toMatch(/data-testid="review-apply"[^>]*disabled|disabled=""[^>]*data-testid="review-apply"/);
  });
  it("stale workspace shows an alert and blocks apply", () => {
    const stale = { ...base, files: [{ ...base.files[0], drift: "stale" as const }] };
    const html = render({ cs: stale });
    expect(html).toContain('role="alert"');
    expect(html).toContain("Applying is blocked");
  });
  it("backend denial is shown verbatim, not as success", () => {
    const html = render({ error: "blocked by S5 (FORBIDDEN_PATH)" });
    expect(html).toContain("blocked by S5 (FORBIDDEN_PATH)");
    expect(html).not.toContain("review-applied");
  });
  it("never offers a merge control", () => {
    expect(render().toLowerCase()).not.toMatch(/>\s*merge\b/);
  });
});
