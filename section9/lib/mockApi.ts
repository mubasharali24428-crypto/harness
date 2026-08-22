import type { AgentPlan, EngineeringBrief, EventEnvelope, Principal } from "@contracts";

const now = () => new Date().toISOString();

let seq = 100;
export const nextSeq = () => ++seq;

export const mockPrincipal: Principal = {
  userId: "8ecf5455-0000-4000-8000-000000000001",
  teamId: "b54e83c5-0000-4000-8000-000000000002",
  role: "admin",
};

export const mockTeams = [
  { id: mockPrincipal.teamId, name: "Mubashar's team", projects: ["demo-repo", "client-api"] },
];

export const mockBrief: EngineeringBrief = {
  id: "brf-001",
  meetingId: "mtg-001",
  projectId: "demo-repo",
  requirements: ["Add /health endpoint returning {ok:true}", "Cover with a unit test"],
  constraints: ["No new dependencies", "Python 3.11+"],
  acceptanceCriteria: ["GET /health returns 200", "pytest passes in CI"],
  risks: ["Route collision with existing middleware"],
  openQuestions: [],
  status: "approved",
};

export const mockPlan: AgentPlan = {
  goal: "Add /health endpoint per approved brief brf-001",
  filesLikelyToChange: ["src/app.py", "tests/test_health.py"],
  steps: [
    "Create route handler returning {ok:true}",
    "Add unit test asserting status 200",
    "Run pytest locally before commit",
  ],
  tests: ["pytest tests/test_health.py"],
  risks: ["Middleware may intercept unknown routes"],
  estimatedCostUsd: 0.041,
  forbiddenPaths: [".env*", "infra/"],
  expectedResult: "GET /health returns 200 with {ok:true}; all tests green",
};

export function mockEvents(): EventEnvelope[] {
  const base = Date.now() - 60_000;
  const mk = (
    type: EventEnvelope["type"],
    payload: Record<string, unknown>,
    offsetMs: number
  ): EventEnvelope => ({
    type,
    payload,
    ts: new Date(base + offsetMs).toISOString(),
    seq: nextSeq(),
    projectId: "demo-repo",
    runId: "run-001",
  });
  return [
    mk("plan_proposed", { planId: "pln-001" }, 0),
    mk("plan_approved", { planId: "pln-001", by: mockPrincipal.userId }, 8_000),
    mk("execution_started", {}, 9_500),
    mk("paused", { reason: "human_edit" }, 30_000),
    mk("resumed", {}, 42_000),
    mk("review_passed", { findings: { passed: true, summary: "Review passed cleanly." } }, 55_000),
  ];
}

export function mockDiff(): { original: string; modified: string } {
  return {
    original: `def health():
    return {"status": "up"}
`,
    modified: `def health():
    """Liveness probe required by brf-001."""
    return {"ok": True, "status": "up"}
`,
  };
}

export function nowIso() {
  return now();
}
