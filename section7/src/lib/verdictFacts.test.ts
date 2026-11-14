// U3 verdict card — fact-to-text under test, including every unknown case.
// The vocabulary is section4 verdict_comment.py's: absent != none, unknown != $0.
import { describe, expect, it } from "vitest";
import { verdictFacts, type VerdictFactsInput } from "./verdictFacts";

function byKey(input: VerdictFactsInput, key: string) {
  const fact = verdictFacts(input).find((f) => f.key === key);
  if (!fact) throw new Error(`missing fact ${key}`);
  return fact;
}

describe("verdictFacts — all unknown", () => {
  const facts = verdictFacts({});

  it("returns the six Week 8 sections in order", () => {
    expect(facts.map((f) => f.key)).toEqual(["plan", "sandbox", "tests", "cost", "risk", "preReview"]);
  });

  it("writes 'not recorded' instead of inventing a fact", () => {
    expect(byKey({}, "plan").value).toBe("not recorded");
    expect(byKey({}, "sandbox").value).toBe("not recorded");
    expect(byKey({}, "risk").value).toBe("not recorded (risk analysis unavailable)");
    expect(byKey({}, "preReview").value).toBe("not recorded");
  });

  it("marks tests and cost unknown without a fake pass or $0", () => {
    const tests = byKey({}, "tests");
    expect(tests.value).toBe("unknown (no recorded tool calls)");
    expect(tests.unknown).toBe(true);
    const cost = byKey({}, "cost");
    expect(cost.value).toBe("actual: unknown");
    expect(cost.details).toEqual(["estimated: unknown"]);
    expect(JSON.stringify(cost)).not.toContain("$0.000000");
  });

  it("marks every all-unknown row as unknown", () => {
    expect(facts.every((f) => f.unknown)).toBe(true);
  });
});

describe("verdictFacts — plan and sandbox", () => {
  it("names the approver and time when both were recorded", () => {
    const fact = byKey(
      { plan: { approved: true, approvedBy: "Ada Lovelace", approvedAt: "2026-11-13 10:00" } },
      "plan",
    );
    expect(fact.value).toBe("Ada Lovelace at 2026-11-13 10:00");
    expect(fact.unknown).toBe(false);
  });

  it("keeps a missing approver/time explicit even when approval is known", () => {
    const fact = byKey({ plan: { approved: true, approvedBy: null, approvedAt: null } }, "plan");
    expect(fact.value).toBe("approved — approver and time not recorded");
    expect(fact.unknown).toBe(true);
  });

  it("records a not-approved plan as recorded, not unknown", () => {
    const fact = byKey({ plan: { approved: false, approvedBy: null, approvedAt: null } }, "plan");
    expect(fact.value).toBe("not approved");
    expect(fact.unknown).toBe(false);
  });

  it("shows a partial sandbox boundary honestly", () => {
    const fact = byKey({ sandbox: { backend: "docker", mode: null } }, "sandbox");
    expect(fact.value).toBe("backend: docker; confinement mode: not recorded");
    expect(fact.unknown).toBe(true);
  });

  it("shows a complete sandbox boundary", () => {
    const fact = byKey({ sandbox: { backend: "docker", mode: "allow-list + OS confinement" } }, "sandbox");
    expect(fact.value).toBe("backend: docker; confinement mode: allow-list + OS confinement");
    expect(fact.unknown).toBe(false);
  });
});

describe("verdictFacts — tests and cost", () => {
  it("distinguishes recorded-no-tests from not-recorded", () => {
    // same words as the Week 8 PR comment (verdict_comment.py _tests_section)
    expect(byKey({ tests: [] }, "tests").value).toBe("not run (NO_TESTS_RUN)");
    expect(byKey({ tests: null }, "tests").value).toBe("unknown (no recorded tool calls)");
  });

  it("lists each recorded command and result", () => {
    const fact = byKey(
      {
        tests: [
          { command: "npm test", result: "pass" },
          { command: "npm run lint", result: "fail" },
        ],
      },
      "tests",
    );
    expect(fact.value).toBe("2 recorded");
    expect(fact.details).toEqual(["npm test: pass", "npm run lint: fail"]);
    expect(fact.unknown).toBe(false);
  });

  it("keeps a missing result unknown instead of calling it passed", () => {
    const fact = byKey({ tests: [{ command: "npm test", result: null }] }, "tests");
    expect(fact.details).toEqual(["npm test: unknown"]);
    expect(fact.unknown).toBe(true);
  });

  it("formats a genuine zero as $0.00 (R1 money rule), never a fabricated one", () => {
    const zero = byKey({ cost: { actualUsd: 0, estimatedUsd: 0 } }, "cost");
    expect(zero.value).toBe("actual: $0.00 (cost_ledger)");
    expect(zero.details).toEqual(["estimated: $0.00 (plan estimate, not actual)"]);
    expect(zero.unknown).toBe(false);
  });

  it("treats negative/non-finite money as unknown", () => {
    expect(byKey({ cost: { actualUsd: -1, estimatedUsd: Number.NaN } }, "cost").value).toBe("actual: unknown");
  });

it("shows '< $0.01' for sub-cent spend (R1 money rule)", () => {
    const fact = byKey({ cost: { actualUsd: 0.000057, estimatedUsd: 0.01 } }, "cost");
    expect(fact.value).toBe("actual: < $0.01 (cost_ledger)");
    expect(fact.details[0]).toBe("estimated: $0.01 (plan estimate, not actual)");
  });

  it("states an absent estimate separately from a known actual", () => {
    const fact = byKey({ cost: { actualUsd: 1.2, estimatedUsd: null } }, "cost");
    expect(fact.value).toBe("actual: $1.20 (cost_ledger)");
    expect(fact.details).toEqual(["estimated: unknown"]);
    expect(fact.unknown).toBe(false);
  });
});

describe("verdictFacts — risk flags and pre-review", () => {
  it("distinguishes 'none flagged' from 'not recorded'", () => {
    const none = byKey({ risk: { recorded: true, flags: [] } }, "risk");
    expect(none.value).toBe("none flagged");
    expect(none.unknown).toBe(false);
    const missing = byKey({ risk: { recorded: false, flags: [] } }, "risk");
    expect(missing.value).toBe("not recorded (risk analysis unavailable)");
    expect(missing.unknown).toBe(true);
  });

  it("lists each flagged path with severity and reason", () => {
    const fact = byKey(
      { risk: { recorded: true, flags: [{ path: "auth/login.ts", severity: "high", reason: "touches auth" }] } },
      "risk",
    );
    expect(fact.value).toBe("1 flagged");
    expect(fact.details).toEqual(["high: auth/login.ts — touches auth"]);
  });

  it("distinguishes a recorded digest with no concerns", () => {
    const none = byKey({ preReview: { recorded: true, concerns: [] } }, "preReview");
    expect(none.value).toBe("recorded; no concerns");
    expect(none.unknown).toBe(false);
    expect(byKey({ preReview: { recorded: false, concerns: [] } }, "preReview").unknown).toBe(true);
  });

  it("lists recorded concerns and the model that produced them", () => {
    const fact = byKey(
      { preReview: { recorded: true, concerns: ["retry may double-charge"], modelUsed: "review-model" } },
      "preReview",
    );
    expect(fact.value).toBe("1 concern");
    expect(fact.details).toEqual(["retry may double-charge", "model: review-model"]);
  });
});
