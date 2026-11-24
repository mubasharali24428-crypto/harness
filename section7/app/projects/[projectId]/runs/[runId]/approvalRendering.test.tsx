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
    // every column labelled in text (short visible + full word for assistive tech): never colour alone
    expect(h).toMatch(/<th scope="col"[^>]*>.*?elev.*?<span class="sr-only">elevated<\/span><\/th>/);
  });
  it("similar past denials: read-only list above the buttons, nothing when empty", () => {
    expect(html("plain")).not.toContain('data-testid="similar-a1"');
    const withCases: Approval = {
      ...approval,
      similarCases: [
        { caseId: "c1", runId: "r-old", outcome: "denied", score: 0.75,
          suggestedRule: "Add /etc/nginx/nginx.conf to scopePaths" },
        { caseId: "c2", runId: "r-older", outcome: "recovered", score: 0.4, suggestedRule: null },
      ],
    };
    const noop = () => undefined;
    const h = renderToStaticMarkup(
      <SharedRunView me="lead" roster={roster} view={deriveCollabView(roster, [withCases], "lead", true)}
        approvals={[withCases]} events={[]} error={null} busy={false} rendering="plain"
        onApply={noop} onJoin={noop} onClaim={noop} onDecide={noop} onHandoff={noop}
        onHandoffDecision={noop} onRedirect={noop} />,
    );
    const list = h.indexOf('data-testid="similar-a1"');
    expect(list).toBeGreaterThan(-1);
    expect(h).toContain("Similar past denials");
    expect(h).toContain("r-old");
    expect(h).toContain("Add /etc/nginx/nginx.conf to scopePaths");
    expect(list).toBeLessThan(h.indexOf("Approve this change"));
    expect(h).not.toContain("Reject this case"); // no dispatch/approve wiring for a lesson
  });

  it("X-08 envelope badge: shown only when the server sends it, and never replaces Approve", () => {
    expect(html("technical")).not.toContain('data-testid="envelope-a1"');
    const withEnvelope = { ...approval, envelope: { tier: "critical", advisory: true as const, autoApprove: false as const } };
    const noop = () => undefined;
    const h = renderToStaticMarkup(
      <SharedRunView me="lead" roster={roster} view={deriveCollabView(roster, [withEnvelope], "lead", true)}
        approvals={[withEnvelope]} events={[]} error={null} busy={false}
        onApply={noop} onJoin={noop} onClaim={noop} onDecide={noop} onHandoff={noop}
        onHandoffDecision={noop} onRedirect={noop} />,
    );
    expect(h).toContain("Risk envelope: critical (advisory; still needs your decision)");
    expect(h).toContain("Approve this change");
  });
});
