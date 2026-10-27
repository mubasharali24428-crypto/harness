import assert from "node:assert/strict";
import { test } from "node:test";

import { sha256Hex } from "../src/core/diff.js";
import {
  runFromEditor,
  RunError,
  type ChangeView,
  type EditorPort,
  type HarnessTransport,
  type PlanOffer,
} from "../src/core/run.js";

const SOURCE = "def a():\n    return 1\n\ndef b():\n    return 3\n";
const PATCH = [
  "--- a/src/a.py",
  "+++ b/src/a.py",
  "@@ -1,5 +1,6 @@",
  " def a():",
  "-    return 1",
  "+    return 2",
  "+    # added",
  " ",
  " def b():",
  "     return 3",
  "",
].join("\n");
const AFTER = "def a():\n    return 2\n    # added\n\ndef b():\n    return 3\n";

function scriptedTransport(overrides: Partial<HarnessTransport> = {}) {
  const calls: string[] = [];
  const goals: string[] = [];
  const doneSignals: string[] = [];
  const change: ChangeView = {
    id: "chg-1",
    path: "src/a.py",
    changeKind: "modified",
    diffSha: sha256Hex(PATCH),
    reviewState: "pending",
  };
  let phase = 0;
  const transport: HarnessTransport = {
    async proposePlan(goal: string): Promise<PlanOffer> {
      goals.push(goal);
      calls.push(`propose:${goal.split("\n")[0]}`);
      return { planId: "plan-1", text: "change return 1 to 2" };
    },
    async approvePlan(planId) {
      calls.push(`approve:${planId}`);
      return { runId: "run-1" };
    },
    async rejectPlan(planId) {
      calls.push(`reject:${planId}`);
    },
    async startRun(runId) {
      calls.push(`start:${runId}`);
    },
    async status() {
      calls.push("status");
      return { state: phase === 0 ? "awaiting_done_signal" : "done" };
    },
    async pendingDiff() {
      calls.push("pending");
      return { diff: PATCH };
    },
    async confirmDone(runId, diffText) {
      calls.push(`done:${runId}:${sha256Hex(diffText).slice(0, 8)}`);
      doneSignals.push(diffText);
      phase = 1;
    },
    async diff() {
      calls.push("diff");
      return [
        {
          path: "src/a.py",
          changeKind: "modified",
          diffSha: change.diffSha,
          patch: PATCH,
          flags: {},
        },
      ];
    },
    async changes() {
      calls.push("changes");
      return [change];
    },
    async reviewChange(runId, changeId, decision, diffSha) {
      calls.push(`review:${runId}:${changeId}:${decision}:${diffSha.slice(0, 8)}`);
    },
    ...overrides,
  };
  return { transport, calls, goals, doneSignals };
}

function fakeEditor(
  initial: Record<string, string>,
  decisions: Record<string, "approve" | "reject"> = {},
  confirm = true,
) {
  const files = new Map(Object.entries(initial));
  const events: string[] = [];
  const shown: Array<{ path: string; before: string | null; after: string | null }> = [];
  const editor: EditorPort = {
    async showPlan() {
      events.push("showPlan");
      return "approve";
    },
    report(state) {
      events.push(`report:${state}`);
    },
    async showDiff(path, before, after, stage) {
      events.push(`diff:${stage}:${path}`);
      shown.push({ path, before, after });
    },
    async showPatch(path) {
      events.push(`patch:${path}`);
    },
    async confirmResult() {
      events.push("confirmResult");
      return confirm;
    },
    async decide(change) {
      events.push(`decide:${change.path}`);
      return decisions[change.path] ?? "approve";
    },
    async readFile(path) {
      return files.has(path) ? files.get(path)! : null;
    },
    async writeFile(path, content) {
      events.push(`write:${path}`);
      files.set(path, content);
    },
    async deleteFile(path) {
      events.push(`delete:${path}`);
      files.delete(path);
    },
  };
  return { editor, files, events, shown };
}

test("the happy path ends with the reviewed bytes on disk and the server naming them", async () => {
  const { transport, calls } = scriptedTransport();
  const { editor, files, events, shown } = fakeEditor({ "src/a.py": SOURCE });

  const outcome = await runFromEditor(transport, editor, {
    task: "Make a return two",
    pollMs: 1,
  });

  assert.equal(outcome.state, "done");
  assert.deepEqual(outcome.applied, ["src/a.py"]);
  assert.deepEqual(outcome.refused, []);
  assert.equal(files.get("src/a.py"), AFTER);
  assert.equal(shown[0]!.after, AFTER);
  assert.deepEqual(calls.slice(0, 4), [
    "propose:Task: Make a return two",
    "approve:plan-1",
    "start:run-1",
    "status",
  ]);
  assert.ok(calls.includes("pending"), "the proposed diff is read before the done-signal");
  assert.ok(calls.some((c) => c.startsWith("done:run-1:")), "the operator's confirmation records the diff");
  assert.ok(outcome.confirmed);
  assert.deepEqual(calls.filter((c) => c.startsWith("review:")), [
    `review:run-1:chg-1:approved:${sha256Hex(PATCH).slice(0, 8)}`,
  ]);
  // Proposed diff shown → result confirmed → reviewed diff shown → decision → write.
  assert.deepEqual(
    events.filter((e) => e.startsWith("diff:")),
    ["diff:proposed:src/a.py", "diff:reviewed:src/a.py"],
  );
  assert.ok(events.indexOf("diff:reviewed:src/a.py") < events.indexOf("decide:src/a.py"));
  assert.ok(events.indexOf("decide:src/a.py") < events.indexOf("write:src/a.py"));
});

test("the done-signal carries exactly the diff the editor showed", async () => {
  const { transport, doneSignals } = scriptedTransport();
  const { editor, shown } = fakeEditor({ "src/a.py": SOURCE });

  await runFromEditor(transport, editor, { task: "x", pollMs: 1 });

  // Displayed twice on purpose: once as the proposed result, once per file for
  // review — and the recorded bytes are the same text both times.
  assert.equal(doneSignals.length, 1);
  assert.equal(doneSignals[0], PATCH);
  assert.equal(shown.length, 2);
  assert.equal(shown[0]!.after, AFTER);
  assert.equal(shown[1]!.after, AFTER);
});

test("declining the result records nothing and writes nothing", async () => {
  const { transport, calls } = scriptedTransport();
  const { editor, files } = fakeEditor({ "src/a.py": SOURCE }, {}, false);

  const outcome = await runFromEditor(transport, editor, { task: "x", pollMs: 1 });

  assert.equal(outcome.state, "awaiting_done_signal");
  assert.equal(outcome.confirmed, false);
  assert.deepEqual(outcome.applied, []);
  assert.match(outcome.refused[0]!.reason, /left awaiting its done-signal/);
  assert.equal(calls.includes("diff"), false, "the recorded diff is never read");
  assert.equal(calls.some((c) => c.startsWith("done:")), false);
  assert.equal(files.get("src/a.py"), SOURCE);
});

test("a rejected plan never starts a run and never writes", async () => {
  const { transport, calls } = scriptedTransport();
  const { editor, files } = fakeEditor({ "src/a.py": SOURCE });
  editor.showPlan = async () => "reject";

  await assert.rejects(
    runFromEditor(transport, editor, { task: "x", pollMs: 1 }),
    (error: unknown) => error instanceof RunError && /rejected/.test((error as Error).message),
  );
  assert.deepEqual(calls, ["propose:Task: x", "reject:plan-1"]);
  assert.equal(files.get("src/a.py"), SOURCE);
});

test("a patch that does not hash to the reviewed digest is refused before review", async () => {
  const { transport, calls } = scriptedTransport();
  const { editor, files, shown, events } = fakeEditor({ "src/a.py": SOURCE });

  // The diff endpoint hands over a patch whose bytes no longer match its digest.
  transport.diff = async () => [
    { path: "src/a.py", changeKind: "modified", diffSha: sha256Hex("other"), patch: PATCH, flags: {} },
  ];

  const outcome = await runFromEditor(transport, editor, { task: "x", pollMs: 1 });
  assert.equal(outcome.applied.length, 0);
  // The only thing on screen is the engine's *proposal* (no digest existed yet);
  // no reviewed diff, no review call, no write.
  assert.equal(shown.length, 1);
  assert.deepEqual(
    events.filter((e) => e.startsWith("diff:")),
    ["diff:proposed:src/a.py"],
  );
  assert.match(outcome.refused[0]!.reason, /digest/);
  assert.equal(calls.filter((c) => c.startsWith("review:")).length, 0);
  assert.equal(files.get("src/a.py"), SOURCE);
});

test("a file that moved after the diff was shown is refused, not overwritten", async () => {
  const { transport } = scriptedTransport();
  const { editor, files, events } = fakeEditor({ "src/a.py": SOURCE });
  const realRead = editor.readFile.bind(editor);
  let reads = 0;
  editor.readFile = async (path: string) => {
    reads += 1;
    const value = await realRead(path);
    // Reads: 1 = the proposed result, 2 = the reviewed snapshot, 3 = the check
    // before writing. The user edits immediately after the reviewed diff is
    // taken, so the bytes on disk are no longer the ones on screen.
    if (reads === 2) files.set(path, SOURCE + "user edit\n");
    return value;
  };

  const outcome = await runFromEditor(transport, editor, { task: "x", pollMs: 1 });
  assert.deepEqual(outcome.applied, []);
  assert.match(outcome.refused[0]!.reason, /changed after the diff/);
  assert.deepEqual(
    events.filter((e) => e.startsWith("diff:")),
    ["diff:proposed:src/a.py", "diff:reviewed:src/a.py"],
    "the reviewed diff really was on screen before the refusal",
  );
  assert.equal(events.some((e) => e.startsWith("write:")), false);
  assert.equal(files.get("src/a.py"), SOURCE + "user edit\n");
});

test("rejecting one file leaves the others alone", async () => {
  const patchB = [
    "--- a/src/b.py",
    "+++ b/src/b.py",
    "@@ -1,2 +1,2 @@",
    " one",
    "-two",
    "+TWO",
    "",
  ].join("\n");
  const { transport } = scriptedTransport();
  transport.diff = async () => [
    { path: "src/a.py", changeKind: "modified", diffSha: sha256Hex(PATCH), patch: PATCH, flags: {} },
    { path: "src/b.py", changeKind: "modified", diffSha: sha256Hex(patchB), patch: patchB, flags: {} },
  ];
  transport.changes = async () => [
    { id: "c1", path: "src/a.py", changeKind: "modified", diffSha: sha256Hex(PATCH), reviewState: "pending" },
    { id: "c2", path: "src/b.py", changeKind: "modified", diffSha: sha256Hex(patchB), reviewState: "pending" },
  ];
  const { editor, files } = fakeEditor({ "src/a.py": SOURCE, "src/b.py": "one\ntwo\n" }, { "src/b.py": "reject" });

  const outcome = await runFromEditor(transport, editor, { task: "x", pollMs: 1 });
  assert.deepEqual(outcome.applied, ["src/a.py"]);
  assert.equal(files.get("src/a.py"), AFTER);
  assert.equal(files.get("src/b.py"), "one\ntwo\n");
});

test("a run that ends in error fails the whole request", async () => {
  const { transport } = scriptedTransport();
  transport.status = async () => ({ state: "error", detail: "worker died" });
  const { editor, files } = fakeEditor({ "src/a.py": SOURCE });
  await assert.rejects(
    runFromEditor(transport, editor, { task: "x", pollMs: 1 }),
    (error: unknown) => error instanceof RunError && /ended in error/.test((error as Error).message),
  );
  assert.equal(files.get("src/a.py"), SOURCE);
});

test("a run that never settles stops at the deadline", async () => {
  const { transport } = scriptedTransport();
  transport.status = async () => ({ state: "executing" });
  const { editor } = fakeEditor({ "src/a.py": SOURCE });
  await assert.rejects(
    runFromEditor(transport, editor, { task: "x", pollMs: 1, deadlineMs: 20 }),
    (error: unknown) => error instanceof RunError && /deadline/.test((error as Error).message),
  );
});

test("context is rendered into the goal, and a reduction is visible there", async () => {
  const { transport, goals } = scriptedTransport();
  transport.changes = async () => [];
  transport.diff = async () => [];
  const { editor } = fakeEditor({});
  await runFromEditor(transport, editor, {
    task: "Fix it",
    context: [
      { kind: "selection", path: "src/a.py", label: "@sel", text: "a".repeat(50), startLine: 1, endLine: 2 },
      { kind: "file", path: "src/b.py", label: "@src/b.py", text: "b".repeat(50) },
    ],
    cap: 60,
    pollMs: 1,
  });
  const goal = goals[0]!;
  assert.match(goal, /^Task: Fix it\n\nContext \(1 item, 50 chars; cap 60\):\n\nContext was reduced: dropped @src\/b\.py \(50 chars\)\./);
  assert.match(goal, /--- @sel \(selection, lines 1-2\) ---/);
  assert.ok(!goal.includes("b".repeat(50)), "the dropped item's text never reaches the wire");
});
