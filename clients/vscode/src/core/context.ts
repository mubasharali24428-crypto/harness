/**
 * Context building for an in-editor request (W7 slice 1).
 *
 * The goal that reaches the server is a string, so "what the model saw" is
 * exactly "what we rendered". Two rules keep that honest:
 *
 * 1. every item is rendered with its label, kind and line span, so a reader of
 *    the audit can tell a selection from a whole file;
 * 2. the block is capped. When it must shrink, the goal *says so* — which
 *    items were dropped and which was truncated — rather than quietly sending
 *    something different from what the editor showed.
 *
 * Nothing here imports `vscode`: the adapter collects items, this module
 * decides what to send, and the tests pin the decision without an editor.
 */

export const MAX_CONTEXT_CHARS = 8000;

export type ContextKind = "selection" | "file" | "symbol" | "hunk";

export interface ContextItem {
  kind: ContextKind;
  /** Workspace-relative POSIX path. Refused if absolute or escaping. */
  path: string;
  /** What the user sees in the picker, e.g. `@src/a.ts` or `@run()`. */
  label: string;
  /** The exact text sent for this item. */
  text: string;
  /** 1-based inclusive span, when the item is a slice of a file. */
  startLine?: number;
  endLine?: number;
}

export interface Truncation {
  item: ContextItem;
  originalChars: number;
  keptChars: number;
}

export interface ContextPlan {
  items: ContextItem[];
  dropped: ContextItem[];
  truncated: Truncation[];
  chars: number;
  cap: number;
}

export class ContextError extends Error {}

/** A context path is workspace-relative, POSIX, and cannot climb out. */
export function assertWorkspacePath(path: string): string {
  if (!path || path.startsWith("/") || /^[A-Za-z]:[\\/]/.test(path)) {
    throw new ContextError(`context path must be workspace-relative: ${path}`);
  }
  const resolved: string[] = [];
  for (const part of path.split(/[\\/]/)) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (!resolved.length) {
        throw new ContextError(`context path escapes the workspace: ${path}`);
      }
      resolved.pop();
      continue;
    }
    resolved.push(part);
  }
  return resolved.join("/");
}

function charCount(text: string): number {
  // Code points, not UTF-16 units, so a truncation never cuts a surrogate pair
  // in half and produces a string the wire format cannot round-trip.
  let n = 0;
  for (const _ of text) n += 1;
  return n;
}

function truncateTo(text: string, limit: number): string {
  let out = "";
  let n = 0;
  for (const ch of text) {
    if (n >= limit) break;
    out += ch;
    n += 1;
  }
  return out;
}

/**
 * Decide what fits. Earliest items win; later ones are dropped first, because
 * the user adds the task's subject before its supporting material. A single
 * item larger than the cap is truncated instead, and the fact is recorded.
 */
export function planContext(items: ContextItem[], cap: number = MAX_CONTEXT_CHARS): ContextPlan {
  if (!Number.isFinite(cap) || cap < 1) throw new ContextError(`invalid context cap: ${cap}`);

  const normalized = items.map((item) => ({
    ...item,
    path: assertWorkspacePath(item.path),
    // Trailing blank lines are not context, and stripping them here keeps the
    // char count equal to what is actually rendered.
    text: item.text.replace(/\n+$/, ""),
  }));

  const sizes = normalized.map((item) => charCount(item.text));
  let total = sizes.reduce((sum, n) => sum + n, 0);

  // Drop from the end while more than one item would remain: the user adds the
  // task's subject before its supporting material, so the subject survives.
  let keep = normalized.length;
  const dropped: ContextItem[] = [];
  while (keep > 1 && total > cap) {
    keep -= 1;
    total -= sizes[keep]!;
    dropped.unshift(normalized[keep]!);
  }

  const kept = normalized.slice(0, keep);
  const truncated: Truncation[] = [];
  if (keep === 1 && total > cap) {
    const only = kept[0]!;
    const text = truncateTo(only.text, cap);
    const keptChars = charCount(text);
    truncated.push({ item: { ...only, text }, originalChars: total, keptChars });
    kept[0] = { ...only, text };
    total = keptChars;
  }

  return { items: kept, dropped, truncated, chars: total, cap };
}

function describe(item: ContextItem): string {
  if (item.startLine && item.endLine) {
    return `${item.kind}, lines ${item.startLine}-${item.endLine}`;
  }
  return item.kind;
}

function reductionNote(plan: ContextPlan): string | null {
  const parts: string[] = [];
  if (plan.dropped.length) {
    const names = plan.dropped
      .map((item) => `${item.label} (${charCount(item.text).toLocaleString("en-US")} chars)`)
      .join(", ");
    parts.push(`dropped ${names}`);
  }
  for (const t of plan.truncated) {
    parts.push(
      `truncated ${t.item.label} to ${t.keptChars.toLocaleString("en-US")} of ` +
        `${t.originalChars.toLocaleString("en-US")} chars`,
    );
  }
  if (!parts.length) return null;
  return `Context was reduced: ${parts.join("; ")}.`;
}

/** The exact string sent as the goal. */
export function renderGoal(task: string, plan: ContextPlan): string {
  const trimmed = task.trim();
  if (trimmed === "") throw new ContextError("a goal needs a task");
  const sections: string[] = [`Task: ${trimmed}`];
  if (!plan.items.length) return sections.join("\n\n");

  const header =
    `Context (${plan.items.length} item${plan.items.length === 1 ? "" : "s"}, ` +
    `${plan.chars.toLocaleString("en-US")} chars; cap ${plan.cap.toLocaleString("en-US")}):`;
  sections.push(header);
  const note = reductionNote(plan);
  if (note) sections.push(note);
  for (const item of plan.items) {
    sections.push(`--- ${item.label} (${describe(item)}) ---\n${item.text}`);
  }
  return sections.join("\n\n");
}

export interface GitHunk {
  path: string;
  /** 1-based index within the file's hunk list. */
  index: number;
  /** The `@@ … @@` line, kept because it is what the user recognises. */
  header: string;
  body: string;
  /** Old-file start line and length from the hunk header. */
  startLine: number;
  lineCount: number;
}

export interface GitDiffSummary {
  hunks: GitHunk[];
  /** Files the diff could not represent as text; never offered as context. */
  binaryPaths: string[];
}

const HUNK_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;
const FILE_RE = /^diff --git a\/(.+) b\/(.+)$/;

/**
 * Split `git diff` output into hunks. Deliberately strict: a line that is not
 * a header, a hunk body continuation or a known git preface is ignored, but a
 * binary section is *recorded* and never offered, because "context" that is
 * silently empty is worse than a refusal.
 */
export function parseGitHunks(diffText: string): GitDiffSummary {
  const hunks: GitHunk[] = [];
  const binaryPaths: string[] = [];
  let path: string | null = null;
  let current: { header: string; startLine: number; lineCount: number; lines: string[] } | null = null;

  const flush = () => {
    if (current && path) {
      hunks.push({
        path,
        index: hunks.filter((h) => h.path === path).length + 1,
        header: current.header,
        body: current.lines.join("\n"),
        startLine: current.startLine,
        lineCount: current.lineCount,
      });
    }
    current = null;
  };

  for (const line of diffText.split("\n")) {
    const fileMatch = FILE_RE.exec(line);
    if (fileMatch) {
      flush();
      path = fileMatch[2]!;
      continue;
    }
    if (line.startsWith("Binary files ") || line === "GIT binary patch") {
      flush();
      if (path) binaryPaths.push(path);
      continue;
    }
    const hunkMatch = HUNK_RE.exec(line);
    if (hunkMatch && path) {
      flush();
      current = {
        header: line,
        startLine: Number(hunkMatch[1]),
        lineCount: hunkMatch[2] === undefined ? 1 : Number(hunkMatch[2]),
        lines: [],
      };
      continue;
    }
    if (current) {
      // Body lines start with ' ', '+', '-' or '\'; anything else ends the hunk.
      if (line === "" || line[0] === " " || line[0] === "+" || line[0] === "-" || line[0] === "\\") {
        if (line !== "") current.lines.push(line);
      } else {
        flush();
      }
    }
  }
  flush();
  return { hunks, binaryPaths };
}

/** Turn parsed hunks into context items the goal renderer can carry. */
export function hunkContextItems(diffText: string): ContextItem[] {
  const { hunks } = parseGitHunks(diffText);
  return hunks.map((hunk) => ({
    kind: "hunk",
    path: hunk.path,
    label: `@hunk:${hunk.path}#${hunk.index}`,
    text: `${hunk.header}\n${hunk.body}`.trimEnd(),
    startLine: hunk.startLine,
    endLine: hunk.startLine + Math.max(hunk.lineCount - 1, 0),
  }));
}
