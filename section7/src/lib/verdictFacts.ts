// U3 review verdict card — pure fact-to-text rules (docs/plans/ui-overhaul-plan-2026-11-13.md §5).
// Vocabulary matches section4's verdict_comment.py so the card and the Week 8 GitHub comment
// say the same things: "not recorded", "none flagged", "unknown", exact scale-6 money. The card
// never upgrades an absent fact to "passed", "$0" or "safe".
import { scrubSecrets } from "@/lib/redact";

export interface VerdictTestRun {
  command: string | null;
  result: string | null;
}

export interface VerdictRiskFlag {
  path: string;
  severity: string;
  reason: string;
}

export interface VerdictFactsInput {
  plan?: { approved: boolean | null; approvedBy: string | null; approvedAt: string | null } | null;
  sandbox?: { backend: string | null; mode: string | null } | null;
  /** null = no endpoint/fact recorded; [] = recorded, but no test runs exist. */
  tests?: VerdictTestRun[] | null;
  cost?: { actualUsd: number | null; estimatedUsd: number | null } | null;
  risk?: { recorded: boolean; flags: VerdictRiskFlag[] } | null;
  preReview?: { recorded: boolean; concerns: string[]; modelUsed?: string | null } | null;
}

export interface VerdictFact {
  key: "plan" | "sandbox" | "tests" | "cost" | "risk" | "preReview";
  label: string;
  value: string;
  details: string[];
  /** True when the primary fact is absent or only partly recorded. */
  unknown: boolean;
}

function money(value: number | null | undefined): string | null {
  // R1 money rule (ui-master-prompt §Visual rules): two decimals from one cent
  // up, "< $0.01" below, unknown when not recorded. A recorded $0 is real data
  // and renders as "$0.00"; null/NaN/negative stay null so the fact says
  // "unknown" instead of a fabricated number.
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  if (value > 0 && value < 0.01) return "< $0.01";
  return `$${value.toFixed(2)}`;
}

function planFact(plan: VerdictFactsInput["plan"]): VerdictFact {
  const label = "Plan approved by";
  if (!plan) return { key: "plan", label, value: "not recorded", details: [], unknown: true };
  const by = plan.approvedBy?.trim() || null;
  const at = plan.approvedAt?.trim() || null;
  if (by || at) {
    const parts = [by ? by : "approver not recorded", at ? `at ${at}` : "time not recorded"];
    return {
      key: "plan",
      label,
      value: parts.join(" "),
      details: [],
      unknown: !by || !at,
    };
  }
  if (plan.approved === true) {
    return { key: "plan", label, value: "approved — approver and time not recorded", details: [], unknown: true };
  }
  if (plan.approved === false) {
    return { key: "plan", label, value: "not approved", details: [], unknown: false };
  }
  return { key: "plan", label, value: "not recorded", details: [], unknown: true };
}

function sandboxFact(sandbox: VerdictFactsInput["sandbox"]): VerdictFact {
  const label = "Sandbox boundary";
  if (!sandbox) return { key: "sandbox", label, value: "not recorded", details: [], unknown: true };
  const backend = sandbox.backend?.trim() || null;
  const mode = sandbox.mode?.trim() || null;
  if (!backend && !mode) return { key: "sandbox", label, value: "not recorded", details: [], unknown: true };
  const parts = [
    `backend: ${backend ?? "not recorded"}`,
    `confinement mode: ${mode ?? "not recorded"}`,
  ];
  return { key: "sandbox", label, value: parts.join("; "), details: [], unknown: !backend || !mode };
}

function testsFact(tests: VerdictFactsInput["tests"]): VerdictFact {
  const label = "Tests";
  if (tests === null || tests === undefined) {
    return { key: "tests", label, value: "unknown (no recorded tool calls)", details: [], unknown: true };
  }
  if (tests.length === 0) {
    return { key: "tests", label, value: "not run (NO_TESTS_RUN)", details: [], unknown: false };
  }
  const details = tests.map((run) => {
    const command = run.command?.trim() || "(command not recorded)";
    const result = run.result?.trim() || "unknown";
    return `${command}: ${result}`;
  });
  const anyUnknown = tests.some((run) => !run.result?.trim());
  return { key: "tests", label, value: `${tests.length} recorded`, details, unknown: anyUnknown };
}

function costFact(cost: VerdictFactsInput["cost"]): VerdictFact {
  const label = "Cost";
  if (!cost) {
    return {
      key: "cost",
      label,
      value: "actual: unknown",
      details: ["estimated: unknown"],
      unknown: true,
    };
  }
  const actual = money(cost.actualUsd);
  const estimated = money(cost.estimatedUsd);
  return {
    key: "cost",
    label,
    value: actual ? `actual: ${actual} (cost_ledger)` : "actual: unknown",
    details: [estimated ? `estimated: ${estimated} (plan estimate, not actual)` : "estimated: unknown"],
    unknown: actual === null,
  };
}

function riskFact(risk: VerdictFactsInput["risk"]): VerdictFact {
  const label = "Risk flags";
  if (!risk || !risk.recorded) {
    return { key: "risk", label, value: "not recorded (risk analysis unavailable)", details: [], unknown: true };
  }
  if (risk.flags.length === 0) return { key: "risk", label, value: "none flagged", details: [], unknown: false };
  return {
    key: "risk",
    label,
    value: `${risk.flags.length} flagged`,
    details: risk.flags.map((f) => {
      const sev = f.severity?.trim() || "unknown";
      const path = f.path?.trim() || "(path not recorded)";
      const reason = f.reason?.trim();
      return `${sev}: ${path}${reason ? ` — ${reason}` : ""}`;
    }),
    unknown: false,
  };
}

function preReviewFact(pre: VerdictFactsInput["preReview"]): VerdictFact {
  const label = "Pre-review concerns";
  if (!pre || !pre.recorded) {
    return { key: "preReview", label, value: "not recorded", details: [], unknown: true };
  }
  if (pre.concerns.length === 0) {
    return { key: "preReview", label, value: "recorded; no concerns", details: [], unknown: false };
  }
  const details = [...pre.concerns];
  if (pre.modelUsed?.trim()) details.push(`model: ${pre.modelUsed.trim()}`);
  return {
    key: "preReview",
    label,
    value: `${pre.concerns.length} concern${pre.concerns.length === 1 ? "" : "s"}`,
    details,
    unknown: false,
  };
}

/** All six verdict facts, in the Week 8 comment's order. Unknown is explicit, never omitted. */
export function verdictFacts(input: VerdictFactsInput): VerdictFact[] {
  // Free text (commands, reasons, concerns, names) comes from the API; mask secret shapes the
  // same way the Week 8 comment builder does before anything reaches the screen.
  return rawFacts(input).map((f) => ({
    ...f,
    value: scrubSecrets(f.value),
    details: f.details.map(scrubSecrets),
  }));
}

function rawFacts(input: VerdictFactsInput): VerdictFact[] {
  return [
    planFact(input.plan),
    sandboxFact(input.sandbox),
    testsFact(input.tests),
    costFact(input.cost),
    riskFact(input.risk),
    preReviewFact(input.preReview),
  ];
}
