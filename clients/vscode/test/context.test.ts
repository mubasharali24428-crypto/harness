import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ContextError,
  hunkContextItems,
  MAX_CONTEXT_CHARS,
  parseGitHunks,
  planContext,
  renderGoal,
  type ContextItem,
} from "../src/core/context.js";

function item(overrides: Partial<ContextItem>): ContextItem {
  return {
    kind: "selection",
    path: "src/a.py",
    label: "@src/a.py",
    text: "print(1)\n",
    ...overrides,
  };
}

test("a goal names every item, its kind and its line span", () => {
  const plan = planContext([
    item({
      label: "@src/a.py:10-12",
      text: "def f():\n    return 1\n",
      startLine: 10,
      endLine: 12,
    }),
    item({ kind: "file", path: "src/b.py", label: "@src/b.py", text: "x = 2\n" }),
  ]);
  assert.equal(
    renderGoal("Make f return two.", plan),
    [
      "Task: Make f return two.",
      "",
      "Context (2 items, 26 chars; cap 8,000):",
      "",
      "--- @src/a.py:10-12 (selection, lines 10-12) ---\ndef f():\n    return 1",
      "",
      "--- @src/b.py (file) ---\nx = 2",
    ].join("\n"),
  );
});

test("without context the goal is just the task", () => {
  assert.equal(renderGoal("Explain this file.", planContext([])), "Task: Explain this file.");
});

test("over the cap, later items are dropped and the goal says which", () => {
  const plan = planContext(
    [
      item({ label: "@first", text: "a".repeat(10) }),
      item({ label: "@second", text: "b".repeat(10) }),
      item({ label: "@third", text: "c".repeat(10) }),
    ],
    20,
  );
  assert.deepEqual(
    plan.items.map((i) => i.label),
    ["@first", "@second"],
  );
  assert.deepEqual(
    plan.dropped.map((i) => i.label),
    ["@third"],
  );
  const goal = renderGoal("do it", plan);
  assert.match(goal, /Context was reduced: dropped @third \(10 chars\)\./);
});

test("a single item larger than the cap is truncated, counted in code points", () => {
  const text = "🔥".repeat(30); // 30 code points, 60 UTF-16 units
  const plan = planContext([item({ label: "@big", text })], 10);
  const kept = plan.items[0]!;
  assert.equal([...kept.text].length, 10);
  assert.equal(plan.truncated.length, 1);
  assert.equal(plan.truncated[0]!.originalChars, 30);
  assert.match(renderGoal("go", plan), /truncated @big to 10 of 30 chars/);
});

test("a context path cannot be absolute or climb out of the workspace", () => {
  assert.throws(() => planContext([item({ path: "/etc/passwd" })]), ContextError);
  assert.throws(() => planContext([item({ path: "../outside.py" })]), ContextError);
  assert.equal(planContext([item({ path: "src/../src/a.py" })]).items[0]!.path, "src/a.py");
});

test("the cap is validated instead of silently becoming zero", () => {
  assert.throws(() => planContext([], 0), ContextError);
  assert.equal(planContext([], MAX_CONTEXT_CHARS).chars, 0);
});

test("git hunks parse into addressable context, and binary files are excluded", () => {
  const diff = [
    "diff --git a/src/a.py b/src/a.py",
    "index 111..222 100644",
    "--- a/src/a.py",
    "+++ b/src/a.py",
    "@@ -3,4 +3,5 @@ def f():",
    " ctx",
    "-old",
    "+new",
    "+more",
    " tail",
    "@@ -20,3 +21,3 @@",
    " x",
    "-y",
    "+z",
    "diff --git a/img.png b/img.png",
    "Binary files a/img.png and b/img.png differ",
    "",
  ].join("\n");

  const summary = parseGitHunks(diff);
  assert.deepEqual(summary.binaryPaths, ["img.png"]);
  assert.equal(summary.hunks.length, 2);
  assert.equal(summary.hunks[0]!.path, "src/a.py");
  assert.equal(summary.hunks[0]!.startLine, 3);
  assert.equal(summary.hunks[0]!.lineCount, 4);
  assert.equal(summary.hunks[1]!.index, 2);

  const items = hunkContextItems(diff);
  assert.equal(items.length, 2);
  assert.equal(items[0]!.label, "@hunk:src/a.py#1");
  assert.equal(items[0]!.startLine, 3);
  assert.equal(items[0]!.endLine, 6);
  assert.match(items[0]!.text, /^-old$/m);
});
