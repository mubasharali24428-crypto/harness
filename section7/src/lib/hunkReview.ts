// Pure decisions behind the Changes tab: which hunks the reviewer marked for
// revert, what the summary line says, and how a diff line maps to a tone.
// Kept out of the component so vitest's node environment can prove the logic
// without a DOM.

import type { CheckpointFile, CheckpointHunk } from "./api";

export interface HunkSelection {
  path: string;
  index: number;
}

/** Stable identity for one hunk. Paths may contain ':', so split at the LAST one. */
export function hunkKey(path: string, index: number): string {
  return `${path}:${index}`;
}

export function parseHunkKey(key: string): HunkSelection | null {
  const cut = key.lastIndexOf(":");
  if (cut <= 0) return null;
  const index = Number(key.slice(cut + 1));
  if (!Number.isInteger(index) || index < 0) return null;
  return { path: key.slice(0, cut), index };
}

export function toggleKey(current: ReadonlySet<string>, key: string): Set<string> {
  const next = new Set(current);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}

export function selectionsFromKeys(keys: Iterable<string>): HunkSelection[] {
  const out: HunkSelection[] = [];
  for (const key of keys) {
    const parsed = parseHunkKey(key);
    if (parsed) out.push(parsed);
  }
  return out;
}

export interface DiffSummary {
  files: number;
  hunks: number;
  additions: number;
  deletions: number;
}

export function summarize(files: CheckpointFile[]): DiffSummary {
  let hunks = 0;
  let additions = 0;
  let deletions = 0;
  for (const file of files) {
    hunks += file.hunks.length;
    for (const hunk of file.hunks) {
      additions += hunk.additions;
      deletions += hunk.deletions;
    }
  }
  return { files: files.length, hunks, additions, deletions };
}

/** "3 files · 5 hunks · +12 −4" (binary files are named, not counted). */
export function summaryLine(files: CheckpointFile[]): string {
  const s = summarize(files);
  const binary = files.filter((f) => f.binary).length;
  const parts = [
    `${s.files} file${s.files === 1 ? "" : "s"}`,
    `${s.hunks} hunk${s.hunks === 1 ? "" : "s"}`,
    `+${s.additions} −${s.deletions}`,
  ];
  if (binary) parts.push(`${binary} binary`);
  return parts.join(" · ");
}

export type LineTone = "add" | "del" | "meta" | "context";

export function lineTone(line: string): LineTone {
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  if (line.startsWith("\\")) return "meta";
  return "context";
}

/** The hunk header names the starting line, which helps reviewers locate it. */
export function hunkLabel(hunk: CheckpointHunk): string {
  const body = hunk.header.replace(/^@@\s*/, "").replace(/\s*@@\s*/, " ").replace(/\s+/g, " ").trim();
  return body || `hunk ${hunk.index + 1}`;
}
