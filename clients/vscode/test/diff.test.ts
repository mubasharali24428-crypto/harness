import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyParsedPatch,
  parseFilePatch,
  PatchError,
  planApply,
  sha256Hex,
  verifyDigest,
} from "../src/core/diff.js";

const SOURCE = "def a():\n    return 1\n\ndef b():\n    return 3\n";

const MODIFY = [
  "diff --git a/src/a.py b/src/a.py",
  "index 111..222 100644",
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

function diffItem(patch: string, overrides: Record<string, unknown> = {}) {
  return {
    path: "src/a.py",
    changeKind: "modified" as const,
    diffSha: sha256Hex(patch),
    patch,
    flags: {},
    ...overrides,
  };
}

test("the digest is the contract: a mismatched patch is refused", () => {
  verifyDigest(MODIFY, sha256Hex(MODIFY));
  assert.throws(() => verifyDigest(MODIFY, sha256Hex(MODIFY + " ")), PatchError);
});

test("a modify patch applies to the current text", () => {
  const plan = planApply(diffItem(MODIFY), SOURCE);
  assert.equal(plan.kind, "modified");
  assert.equal(plan.content, "def a():\n    return 2\n    # added\n\ndef b():\n    return 3\n");
});

test("a file that drifted a little relocates only on a unique match", () => {
  const shifted = "header = 1\n\n" + SOURCE;
  const plan = planApply(diffItem(MODIFY), shifted);
  assert.equal(
    plan.content,
    "header = 1\n\ndef a():\n    return 2\n    # added\n\ndef b():\n    return 3\n",
  );
});

test("ambiguous relocation is refused, not guessed", () => {
  // The declared position no longer matches (a header line was added), and the
  // block appears twice in the window: no choice is safe.
  const twice = "header = 1\n\n" + SOURCE + "\n" + SOURCE;
  assert.throws(() => planApply(diffItem(MODIFY), twice), /ambiguous/);
});

test("a changed context line is refused", () => {
  const tampered = SOURCE.replace("    return 3", "    return 4");
  assert.throws(() => planApply(diffItem(MODIFY), tampered), /not found/);
});

test("a hunk whose header lies about its size is refused at parse time", () => {
  const lying = MODIFY.replace("@@ -1,5 +1,6 @@", "@@ -1,4 +1,6 @@");
  assert.throws(() => parseFilePatch(lying), /body has/);
});

test("added and deleted files are explicit", () => {
  const add = [
    "diff --git a/src/new.py b/src/new.py",
    "new file mode 100644",
    "--- /dev/null",
    "+++ b/src/new.py",
    "@@ -0,0 +1,2 @@",
    "+x = 1",
    "+y = 2",
    "",
  ].join("\n");
  const addPlan = planApply(
    { path: "src/new.py", changeKind: "added", diffSha: sha256Hex(add), patch: add, flags: {} },
    null,
  );
  assert.equal(addPlan.content, "x = 1\ny = 2");

  const del = [
    "diff --git a/src/a.py b/src/a.py",
    "deleted file mode 100644",
    "--- a/src/a.py",
    "+++ /dev/null",
    "@@ -1,5 +0,0 @@",
    "-def a():",
    "-    return 1",
    "-",
    "-def b():",
    "-    return 3",
    "",
  ].join("\n");
  const delPlan = planApply(
    { path: "src/a.py", changeKind: "deleted", diffSha: sha256Hex(del), patch: del, flags: {} },
    SOURCE,
  );
  assert.equal(delPlan.content, null);
});

test("an added patch against a file with content is refused", () => {
  const add = [
    "--- /dev/null",
    "+++ b/src/new.py",
    "@@ -0,0 +1,1 @@",
    "+x = 1",
    "",
  ].join("\n");
  assert.throws(() => applyParsedPatch("already here\n", parseFilePatch(add)), /already has content/);
});

test("omitted, binary, symlink and rename changes are refused with reasons", () => {
  const base = diffItem(MODIFY);
  assert.throws(() => planApply({ ...base, patchOmitted: "binary" }, SOURCE), /omitted/);
  assert.throws(() => planApply({ ...base, flags: { binary: true } }, SOURCE), /binary/);
  assert.throws(() => planApply({ ...base, flags: { symlink: true } }, SOURCE), /symlink/);
  assert.throws(() => planApply({ ...base, changeKind: "renamed" }, SOURCE), /rename/);
});

test("a patch that names another file is refused", () => {
  assert.throws(() => planApply({ ...diffItem(MODIFY), path: "src/other.py" }, SOURCE), /names/);
  assert.throws(() => planApply({ ...diffItem(MODIFY), path: "../escape.py" }, SOURCE), /escapes|relative/);
});

test("a path traversal in the patch header is refused", () => {
  const evil = MODIFY.replace("--- a/src/a.py", "--- a/../../etc/passwd").replace(
    "+++ b/src/a.py",
    "+++ b/../../etc/passwd",
  );
  assert.throws(
    () => planApply({ ...diffItem(evil), path: "../../etc/passwd" }, SOURCE),
    /escapes|relative/,
  );
});

test("a missing trailing newline is preserved through the patch", () => {
  const noNewline = [
    "--- a/src/a.py",
    "+++ b/src/a.py",
    "@@ -1,1 +1,1 @@",
    "-old",
    "\\ No newline at end of file",
    "+new",
    "\\ No newline at end of file",
    "",
  ].join("\n");
  const parsed = parseFilePatch(noNewline);
  assert.equal(applyParsedPatch("old", parsed), "new");
});
