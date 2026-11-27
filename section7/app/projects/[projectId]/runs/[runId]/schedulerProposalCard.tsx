"use client";

// Task 18: read-only view of a stored W31 memory-tier proposal. Nothing here dispatches,
// applies or executes anything — the server flag HARNESS_SCHEDULER_PROPOSALS=1 gates the data,
// and the proposal is a human-review artifact by construction (proposalOnly/requiresHumanApproval).
import * as React from "react";
import type { SchedulerProposal } from "./collabApi";

const muted = { color: "var(--muted)" } as const;
const TIERS = ["hot", "warm", "cold"] as const;

export function SchedulerProposalCard({ proposal }: { proposal: SchedulerProposal }) {
  const counts = TIERS.map((tier) => ({
    tier,
    count: proposal.proposal.placements.filter((p) => p.tier === tier).length,
  }));
  return (
    <section data-testid="scheduler-proposal" style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10, marginBottom: 8 }}>
      <h3 style={{ fontSize: 13, margin: "0 0 4px" }}>Memory-tier proposal (W31)</h3>
      <p role="status" style={{ ...muted, margin: "4px 0" }}>
        Proposal only — nothing was dispatched. A human must approve before any change.
      </p>
      <p style={{ margin: "4px 0" }}>
        Tier breakdown: {counts.map((c) => `${c.tier} ${c.count}`).join(" · ")}
        <span style={muted}> (schema {proposal.schema})</span>
      </p>
      <p style={{ margin: "4px 0" }}>
        Validator: {proposal.valid ? "no violations" : `${proposal.violations.length} violation(s)`}
      </p>
      {!proposal.valid && (
        <ul style={{ margin: "2px 0", paddingLeft: 18 }}>
          {proposal.violations.map((v, i) => <li key={`${i}-${v}`}>{v}</li>)}
        </ul>
      )}
      {!proposal.proposalOnly && <p style={muted}>Server did not mark this proposal as proposal-only.</p>}
    </section>
  );
}
