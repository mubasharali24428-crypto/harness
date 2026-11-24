// X-07 Plain / X-21 Decision card on the shared-run approval card (node env, static render).
import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Approval, Roster } from "./collabApi";
import { deriveCollabView } from "./collabModel";
import { SharedRunView, type SharedRunViewProps } from "./sharedRunPanel";

const roster: Roster = { participants: [{ userId: "lead", role: "leader" }], leader: "lead", pendingHandoff: null };
const approval: Approval = {
  id: "a1", runId: "r1", action: "modify", paths: ["deploy/.env"], summary: "rotate key", diff: "-OLD",
  digest: "d".repeat(64), state: "pending", requester: "eng2", decidedBy: null, decidedAt: null,
  outcome: null, expiresAt: "2026-12-01T00:00:00Z",
  plain: "The agent wants to change 1 file (deploy/.env). This touches a secrets or credentials file.",
  decisionGrid: {
    dominantBand: "critical",
    cells: [
      { family: "file_write", band: "moderate", label: "File write" },
      { family: "credentials", band: "critical", label: "Credentials / secrets" },
    ],
  },
};

function html(rendering?: SharedRunViewProps["rendering"]) {
  const noop = () => undefined;
  return renderToStaticMarkup(
    <SharedRunView me="lead" roster={roster} view={deriveCollabView(roster, [approval], "lead", true)}
      approvals={[approval]} events={[]} error={null} busy={false} rendering={rendering}
      onApply={noop} onJoin={noop} onClaim={noop} onDecide={noop} onHandoff={noop}
      onHandoffDecision={noop} onRedirect={noop} />,
  );
}

describe("approval rendering by reader", () => {
  it("technical (default) shows neither translation", () => {
    const h = html();
    expect(h).not.toContain('data-testid="plain-a1"');
    expect(h).not.toContain('data-testid="grid-a1"');
  });
  it("plain puts the sentence first and keeps the technical record", () => {
    const h = html("plain");
    expect(h).toContain("This touches a secrets or credentials file.");
    expect(h.indexOf('data-testid="plain-a1"')).toBeLessThan(h.indexOf("rotate key"));
    expect(h).toContain("-OLD"); // diff still one look away, never replaced
  });
  it("decision card: dominant band in text, read before Approve", () => {
    const h = html("decision");
    const dominant = h.indexOf('data-testid="grid-a1-dominant"');
    expect(dominant).toBeGreaterThan(-1);
    expect(dominant).toBeLessThan(h.indexOf("Approve this change"));
    expect(h).toMatch(/Overall risk:.*critical/);
    expect(h).toContain("Credentials / secrets");
    expect(h).toContain("<th scope=\"col\">elevated</th>"); // every column labelled: never colour alone
  });
});
