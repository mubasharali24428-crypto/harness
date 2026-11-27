// Task 18: the read-only W31 proposal card and its read-only client.
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { getSchedulerProposal, type SchedulerProposal } from "./collabApi";
import { SchedulerProposalCard } from "./schedulerProposalCard";

afterEach(() => vi.unstubAllGlobals());

const proposal: SchedulerProposal = {
  runId: "r1", schema: "w31-tier-ttl/1",
  proposal: {
    schema: "w31-tier-ttl/1", status: "optimal", proposalOnly: true, requiresHumanApproval: true,
    placements: [
      { id: "m1", tier: "hot", ttl_hours: 24 },
      { id: "m2", tier: "warm", ttl_hours: null },
    ],
  },
  violations: [], valid: true, proposalOnly: true, requiresHumanApproval: true, createdBy: "boss",
};

describe("SchedulerProposalCard", () => {
  it("shows tier breakdown, validator status and the proposal-only banner", () => {
    const h = renderToStaticMarkup(<SchedulerProposalCard proposal={proposal} />);
    expect(h).toContain("hot 1 · warm 1 · cold 0");
    expect(h).toContain("Validator: no violations");
    expect(h).toContain("w31-tier-ttl/1");
    expect(h).toMatch(/Proposal only.*nothing was dispatched/i);
  });

  it("lists validator violations when the proposal is invalid", () => {
    const bad: SchedulerProposal = { ...proposal, valid: false,
      violations: ["m1: 48h needs billing_approved"] };
    const h = renderToStaticMarkup(<SchedulerProposalCard proposal={bad} />);
    expect(h).toContain("1 violation(s)");
    expect(h).toContain("m1: 48h needs billing_approved");
  });

  it("never renders an interactive control (no dispatch/apply wiring)", () => {
    const h = renderToStaticMarkup(<SchedulerProposalCard proposal={proposal} />).toLowerCase();
    expect(h).not.toContain("<button");
    expect(h).not.toContain("<a ");
    expect(h).not.toMatch(/aria-label="(apply|dispatch|execute)/i);
  });
});

describe("getSchedulerProposal", () => {
  it("GETs the run's latest proposal with the session token", async () => {
    const f = vi.fn(async () => ({
      ok: true, status: 200, text: async () => JSON.stringify({ runId: "r1", latest: proposal }),
    }));
    vi.stubGlobal("fetch", f);
    await expect(getSchedulerProposal("r1", "tok")).resolves.toEqual({ runId: "r1", latest: proposal });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/v1\/orchestrator\/runs\/r1\/scheduler-proposals$/);
    expect(init.method).toBeUndefined(); // GET
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer tok");
  });
});
