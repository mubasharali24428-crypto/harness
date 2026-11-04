import assert from "node:assert/strict";
import { test } from "node:test";

import { DecisionGate } from "../src/core/gate.js";
import { parseSummary, priceSummary, renderSummary } from "../src/core/summary.js";

function raw(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema: 1,
    basis: "recorded from the agent's own tool calls and token usage; the harness re-ran nothing",
    files: [{ path: "src/a.py", change: "modified", added: 3, removed: 1 }],
    tests: { known: true, runs: [{ command: "pytest -q", result: "passed" }], note: "exit status only" },
    failures: [],
    cost: { usd: null, source: null, model: "anthropic/claude-haiku-5.5", inputTokens: 7045, outputTokens: 64 },
    elapsedSeconds: 12.3,
    uncertainty: [{ code: "COST_UNPRICED", text: "tokens are known but no price covers this model" }],
    ...over,
  };
}

test("a summary from the runner is accepted, and anything else is refused rather than guessed", () => {
  assert.ok(parseSummary(raw()));
  assert.equal(parseSummary(null), null);
  assert.equal(parseSummary({ ...raw(), schema: 2 }), null);
  assert.equal(parseSummary({ ...raw(), files: "src/a.py" }), null);
  assert.equal(parseSummary({ ...raw(), files: [{ change: "modified" }] }), null);
  assert.equal(parseSummary({ ...raw(), tests: { known: true } }), null);
});

test("pricing fills the cost and removes only the unpriced note", () => {
  const summary = parseSummary(raw({
    uncertainty: [
      { code: "COST_UNPRICED", text: "no price" },
      { code: "NO_TESTS_RUN", text: "no test command was run" },
    ],
  }))!;
  const priced = priceSummary(summary, { priced: true, usd: 0.000736 });
  assert.equal(priced.cost.usd, 0.000736);
  assert.equal(priced.cost.source, "rate card");
  assert.deepEqual(priced.uncertainty.map((u) => u.code), ["NO_TESTS_RUN"]);
});

test("a model the rate card does not cover stays unknown, never zero", () => {
  const summary = parseSummary(raw())!;
  for (const estimate of [{ priced: false, usd: 0 }, null, { priced: true, usd: Number.NaN }]) {
    const result = priceSummary(summary, estimate);
    assert.equal(result.cost.usd, null);
    assert.equal(result.uncertainty.length, 1);
    assert.doesNotMatch(renderSummary(result), /\$0/);
  }
});

test("the text names files, tests, failures, cost, time and what is still unknown", () => {
  const text = renderSummary(priceSummary(parseSummary(raw())!, { priced: true, usd: 0.000736 }));
  assert.match(text, /Changed files \(1\):\n {2}src\/a\.py {2}modified {2}\+3 −1/);
  assert.match(text, /Tests:\n {2}pytest -q {2}passed\n {2}\(exit status only\)/);
  assert.match(text, /Failures: none recorded/);
  assert.match(text, /Cost: \$0\.000736 · 7,045 in \/ 64 out · anthropic\/claude-haiku-5\.5 · rate card/);
  assert.match(text, /Time: 12\.3 s/);
  assert.doesNotMatch(text, /Still unknown/);
  assert.match(text, /re-ran nothing\./);
});

test("unknowns are written as unknowns", () => {
  const text = renderSummary(parseSummary(raw({
    files: [],
    tests: { known: false, runs: [] },
    failures: [{ kind: "test", what: "pytest -q failed" }],
    elapsedSeconds: null,
    uncertainty: [{ code: "TOOL_CALLS_NOT_SURFACED", text: "which tests ran is unknown" }],
  }))!);
  assert.match(text, /Changed files: none/);
  assert.match(text, /Tests: unknown \(the engine did not report its tool calls\)/);
  assert.match(text, /Failures:\n {2}• pytest -q failed/);
  assert.match(text, /Cost: unknown \(7,045 in \/ 64 out tokens · anthropic\/claude-haiku-5\.5\)/);
  assert.match(text, /Time: unknown/);
  assert.match(text, /Still unknown:\n {2}• which tests ran is unknown/);
});

test("a long file list is cut with a count, not silently", () => {
  const files = Array.from({ length: 11 }, (_, i) => ({ path: `f${i}.py`, change: "added", added: 1, removed: 0 }));
  const text = renderSummary(parseSummary(raw({ files }))!);
  assert.match(text, /Changed files \(11\):/);
  assert.match(text, /f7\.py/);
  assert.doesNotMatch(text, /f8\.py/);
  assert.match(text, /and 3 more/);
});

// ------------------------------------------------------------------ the decision gate

test("a keybinding answers the decision that is waiting now", async () => {
  const gate = new DecisionGate();
  const decision = gate.ask(() => undefined);
  assert.equal(gate.pending, true);
  assert.equal(gate.answer("approve"), true);
  assert.equal(await decision, "approve");
  assert.equal(gate.pending, false);
  assert.equal(gate.answer("approve"), false, "nothing is waiting any more");
});

test("a button from the previous file cannot answer the next file", async () => {
  const gate = new DecisionGate();
  const ids: number[] = [];
  const first = gate.ask((id) => ids.push(id));
  gate.answer("approve", ids[0]);
  assert.equal(await first, "approve");

  const second = gate.ask((id) => ids.push(id));
  assert.equal(gate.answer("approve", ids[0]), false, "a stale prompt is ignored");
  assert.equal(gate.pending, true);
  assert.equal(gate.answer("reject", ids[1]), true);
  assert.equal(await second, "reject");
});

test("skip-all rejects the rest of the run without asking again, and a new run asks again", async () => {
  const gate = new DecisionGate();
  const first = gate.ask(() => undefined);
  gate.answer("stop");
  assert.equal(await first, "reject");

  let asked = false;
  assert.equal(await gate.ask(() => { asked = true; }), "reject");
  assert.equal(asked, false);

  gate.reset();
  const again = gate.ask(() => { asked = true; });
  assert.equal(asked, true);
  gate.answer("approve");
  assert.equal(await again, "approve");
});

test("a new run releases a decision left waiting by a dead one as a rejection", async () => {
  const gate = new DecisionGate();
  const left = gate.ask(() => undefined);
  gate.reset();
  assert.equal(await left, "reject");
  assert.equal(gate.pending, false);
});
