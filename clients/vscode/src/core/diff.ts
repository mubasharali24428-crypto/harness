/**
 * Diff consumption for the editor (W7 slice 2).
 *
 * The server is the authority on *what changed*: `/runs/{id}/diff` returns one
 * patch per file plus `diffSha = sha256(patch)`, and `/runs/{id}/changes`
 * records a review state per file. The editor's job is narrower and stricter:
 *
 * * recompute the digest and refuse to show a patch that does not match the
 *   digest the review will be bound to;
 * * apply the patch to the file's **current** text with exact context
 *   matching — no fuzz, no guessing;
 * * refuse anything it cannot reproduce byte-for-byte (binary, rename-only,
 *   symlink, omitted patch, path outside the workspace).
 *
 * The same functions produce the "after" text shown in the inline diff and the
 * bytes written on apply, so the review and the write cannot disagree.
 */

import { createHash } from "node:crypto";

import { assertWorkspacePath, ContextError } from "./context.js";

export class PatchError extends Error {}

export type ChangeKind = "added" | "deleted" | "modified" | "renamed";

export interface RunDiffItem {
  path: string;
  changeKind: ChangeKind | string;
  diffSha: string;
  patch: string | null;
  patchOmitted?: string | null;
  flags?: Record<string, boolean | undefined>;
}

interface Hunk {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  /** Body lines, each with its prefix character (' ', '+', '-', '\'). */
  body: string[];
}

export interface ParsedPatch {
  oldPath: string | null;
  newPath: string | null;
  kind: "added" | "deleted" | "modified";
  hunks: Hunk[];
}

export interface ApplyPlan {
  path: string;
  /** New file content, or null when the change deletes the file. */
  content: string | null;
  kind: "added" | "deleted" | "modified";
}

export function sha256Hex(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

/** The review is bound to `diffSha`; the editor shows only what hashes to it. */
export function verifyDigest(patch: string, diffSha: string): void {
  const actual = sha256Hex(patch);
  if (actual !== diffSha) {
    throw new PatchError(
      `patch does not match the reviewed digest (expected ${diffSha}, got ${actual})`,
    );
  }
}

function stripPrefix(raw: string): string | null {
  if (raw.startsWith("a/") || raw.startsWith("b/")) return raw.slice(2);
  return raw;
}

const HUNK_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;

/** Parse a single-file unified patch. Throws on anything it cannot apply. */
export function parseFilePatch(patch: string): ParsedPatch {
  if (patch.startsWith("GIT binary patch") || /^Binary files? /m.test(patch)) {
    throw new PatchError("binary patches cannot be applied as text");
  }
  const lines = patch.split("\n");
  let oldPath: string | null = null;
  let newPath: string | null = null;
  const hunks: Hunk[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i]!;
    if (line.startsWith("diff --git ")) {
      // Paths are taken from ---/+++ below; a bare diff line is not trusted.
      i += 1;
      continue;
    }
    if (line.startsWith("--- ")) {
      const raw = line.slice(4).trim();
      oldPath = raw === "/dev/null" ? null : stripPrefix(raw);
      i += 1;
      continue;
    }
    if (line.startsWith("+++ ")) {
      const raw = line.slice(4).trim();
      newPath = raw === "/dev/null" ? null : stripPrefix(raw);
      i += 1;
      continue;
    }
    const match = HUNK_RE.exec(line);
    if (match) {
      const hunk: Hunk = {
        oldStart: Number(match[1]),
        oldLines: match[2] === undefined ? 1 : Number(match[2]),
        newStart: Number(match[3]),
        newLines: match[4] === undefined ? 1 : Number(match[4]),
        body: [],
      };
      i += 1;
      while (i < lines.length) {
        const bodyLine = lines[i]!;
        if (HUNK_RE.test(bodyLine) || bodyLine.startsWith("diff --git ")) break;
        if (bodyLine === "") {
          // The trailing element of a patch that ends with a newline, or a
          // separator before the next file — never a "line of the hunk" on its
          // own. Anything else blank is a context line whose space was stripped.
          if (i === lines.length - 1) break;
          hunk.body.push(" ");
        } else if (
          bodyLine[0] === " " ||
          bodyLine[0] === "+" ||
          bodyLine[0] === "-" ||
          bodyLine[0] === "\\"
        ) {
          hunk.body.push(bodyLine);
        } else {
          // Unknown content ends the hunk; the caller sees a count mismatch if
          // it mattered, which is a refusal rather than a silent skip.
          break;
        }
        i += 1;
      }
      verifyHunkCounts(hunk);
      hunks.push(hunk);
      continue;
    }
    i += 1;
  }

  if (!hunks.length) {
    throw new PatchError(
      oldPath === null && newPath === null
        ? "the patch has no file headers"
        : "the patch has no hunks (rename-only or mode-only changes cannot be applied as text)",
    );
  }
  const kind: ParsedPatch["kind"] =
    oldPath === null ? "added" : newPath === null ? "deleted" : "modified";
  return { oldPath, newPath, kind, hunks };
}

function verifyHunkCounts(hunk: Hunk): void {
  let oldCount = 0;
  let newCount = 0;
  for (const line of hunk.body) {
    if (line.startsWith(" ")) {
      oldCount += 1;
      newCount += 1;
    } else if (line.startsWith("-")) {
      oldCount += 1;
    } else if (line.startsWith("+")) {
      newCount += 1;
    }
  }
  if (oldCount !== hunk.oldLines || newCount !== hunk.newLines) {
    throw new PatchError(
      `hunk header says -${hunk.oldLines}/+${hunk.newLines} but the body has ` +
        `-${oldCount}/+${newCount}`,
    );
  }
}

interface SplitText {
  lines: string[];
  trailingNewline: boolean;
}

function splitText(text: string): SplitText {
  if (text === "") return { lines: [], trailingNewline: false };
  const trailingNewline = text.endsWith("\n");
  const lines = text.split("\n");
  if (trailingNewline) lines.pop();
  return { lines, trailingNewline };
}

function joinText(split: SplitText): string {
  if (!split.lines.length) return "";
  return split.lines.join("\n") + (split.trailingNewline ? "\n" : "");
}

/** Lines this hunk expects to find in the source, in order. */
function sourcePattern(hunk: Hunk): string[] {
  const out: string[] = [];
  for (const line of hunk.body) {
    if (line.startsWith(" ") || line.startsWith("-")) out.push(line.slice(1));
  }
  return out;
}

function matchesAt(source: string[], position: number, pattern: string[]): boolean {
  if (position < 0 || position + pattern.length > source.length) return false;
  for (let i = 0; i < pattern.length; i += 1) {
    if (source[position + i] !== pattern[i]) return false;
  }
  return true;
}

const RELOCATION_WINDOW = 100;

/**
 * Where this hunk belongs. The declared position wins; otherwise an exact
 * match within a small window is allowed **only if it is unique** — two
 * candidates means the file has drifted and any choice could be wrong.
 */
function locate(source: string[], hunk: Hunk): number {
  const pattern = sourcePattern(hunk);
  const expected = Math.max(hunk.oldStart - 1, 0);
  if (matchesAt(source, expected, pattern)) return expected;
  const candidates: number[] = [];
  const low = Math.max(0, expected - RELOCATION_WINDOW);
  const high = Math.min(source.length, expected + RELOCATION_WINDOW);
  for (let pos = low; pos <= high; pos += 1) {
    if (pos !== expected && matchesAt(source, pos, pattern)) candidates.push(pos);
  }
  if (candidates.length === 1) return candidates[0]!;
  throw new PatchError(
    candidates.length
      ? `hunk context is ambiguous near line ${hunk.oldStart}`
      : `hunk context not found near line ${hunk.oldStart}`,
  );
}

/**
 * Apply one parsed patch to the file's current text. `current` is null for a
 * file the change creates.
 */
export function applyParsedPatch(current: string | null, parsed: ParsedPatch): string | null {
  if (parsed.kind === "added" && current !== null && current !== "") {
    throw new PatchError("the change creates a file that already has content");
  }
  const source = splitText(current ?? "");
  const out: string[] = [];
  let cursor = 0;
  let trailingNewline = source.trailingNewline;

  for (const hunk of parsed.hunks) {
    const position = locate(source.lines, hunk);
    // Everything strictly before the hunk is untouched.
    while (cursor < position) {
      out.push(source.lines[cursor]!);
      cursor += 1;
    }
    for (const line of hunk.body) {
      const tag = line[0];
      const text = line.slice(1);
      if (tag === " ") {
        if (source.lines[cursor] !== text) {
          throw new PatchError(`context mismatch at source line ${cursor + 1}`);
        }
        out.push(text);
        cursor += 1;
      } else if (tag === "-") {
        if (source.lines[cursor] !== text) {
          throw new PatchError(`deleted line ${cursor + 1} does not match the file`);
        }
        cursor += 1;
      } else if (tag === "+") {
        out.push(text);
      } else if (tag === "\\") {
        // "\ No newline at end of file" — it belongs to the line just emitted.
        if (out.length) trailingNewline = false;
      }
    }
  }
  while (cursor < source.lines.length) {
    out.push(source.lines[cursor]!);
    cursor += 1;
  }

  if (parsed.kind === "deleted") {
    if (out.length) {
      throw new PatchError("the change deletes a file but the patch left content behind");
    }
    return null;
  }
  return joinText({ lines: out, trailingNewline });
}

function refuse(reason: string): never {
  throw new PatchError(reason);
}

/**
 * Turn a server diff item into the exact bytes to write, refusing anything
 * that is not the reviewed, applicable patch for this path.
 */
export function planApply(item: RunDiffItem, current: string | null): ApplyPlan {
  const path = assertWorkspacePath(item.path);
  if (item.patchOmitted) {
    refuse(`the server omitted this patch (${item.patchOmitted}); nothing to review`);
  }
  if (item.flags?.binary) refuse("binary changes cannot be applied in the editor");
  if (item.flags?.symlink) refuse("symlink changes cannot be applied in the editor");
  if (item.changeKind === "renamed") {
    refuse("rename-only changes cannot be applied in the editor");
  }
  if (item.patch == null) refuse("the server returned no patch for this change");
  verifyDigest(item.patch, item.diffSha);

  const parsed = parseFilePatch(item.patch);
  const declared = parsed.newPath ?? parsed.oldPath;
  if (declared && declared !== path) {
    throw new PatchError(`the patch names ${declared}, but the change names ${path}`);
  }
  if (parsed.kind === "added" && item.changeKind !== "added") {
    throw new PatchError("the patch creates a file but the change is not an addition");
  }
  if (parsed.kind === "deleted" && item.changeKind !== "deleted") {
    throw new PatchError("the patch deletes a file but the change is not a deletion");
  }
  const content = applyParsedPatch(current, parsed);
  return { path, content, kind: parsed.kind };
}

export { ContextError };

/**
 * Split a multi-file unified diff into per-file patches (for display before the
 * run is completed). Everything from a `diff --git` line to the next one is one
 * patch; a diff without file headers becomes a single unnamed patch.
 */
export function splitPatchByFile(diffText: string): Array<{ path: string; patch: string }> {
  const lines = diffText.split("\n");
  const chunks: string[][] = [];
  for (const line of lines) {
    if (line.startsWith("diff --git ")) chunks.push([line]);
    else if (chunks.length) chunks[chunks.length - 1]!.push(line);
    else if (line.trim()) chunks.push([line]);
  }
  return chunks.map((chunk) => {
    const header = /^diff --git a\/(.+) b\/(.+)$/.exec(chunk[0] ?? "");
    let path = header?.[2] ?? "";
    if (!path) {
      const plus = chunk.find((line) => line.startsWith("+++ "));
      if (plus) path = stripPrefix(plus.slice(4).trim()) ?? "";
    }
    return { path, patch: chunk.join("\n").trimEnd() };
  });
}
