// Sprint 3 / Task 15 — the single approval mock used by the browser specs and by the
// openapi parity test. Keep both in sync: the parity test fails if this drifts from
// section4's documented ApprovalRequest contract.
import type { Approval } from "../app/projects/[projectId]/runs/[runId]/collabApi";

export const APPROVAL: Approval = {
  id: "a1", runId: "r1", action: "modify", paths: ["deploy/.env"], summary: "rotate the API key",
  diff: "-OLD_KEY\n+NEW_KEY", digest: "d".repeat(64), state: "pending", requester: "eng2",
  decidedBy: null, decidedAt: null, outcome: null, expiresAt: "2026-12-01T00:00:00Z",
  plain: "The agent wants to change 1 file (deploy/.env). This touches a secrets or credentials file.",
  decisionGrid: {
    dominantBand: "critical",
    cells: [
      { family: "file_write", band: "moderate", label: "File write" },
      { family: "credentials", band: "critical", label: "Credentials / secrets" },
    ],
  },
  // Task 14: the same read-only lesson list the collab API now sends.
  similarCases: [
    { caseId: "c1", runId: "r-old", outcome: "denied", score: 0.75,
      suggestedRule: "Add deploy/.env to scopePaths" },
  ],
};
