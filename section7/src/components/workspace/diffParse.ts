// Unified-diff parsing and per-file stat labels for the diff surfaces.
// Pure functions — never throws on garbage input, so the pane can always
// render an honest "(unparseable diff)" block instead of failing.
//
// E0 2026-11-21: moved verbatim out of previewData.ts (a misnamed R1 module).

/** One unified-diff line, classified for rendering. Pure. */
export type DiffLineTone = "add" | "del" | "meta" | "context";

export interface DiffLine {
  tone: DiffLineTone;
  /** File-relative old/new line numbers where the diff records them. */
  oldNo: number | null;
  newNo: number | null;
  text: string;
}

export interface DiffFile {
  /** Path after "+++ b/…"; "unknown file" when the header is missing. */
  path: string;
  lines: DiffLine[];
}

/**
 * Minimal unified-diff parser: files from ---/+++ headers, hunks from @@, the
 * rest classified by prefix. Never throws on garbage — malformed input renders
 * as one meta block so the pane can say the diff text was unparseable.
 */
export function parseUnifiedDiff(diffText: string): DiffFile[] {
  const files: DiffFile[] = [];
  let current: DiffFile | null = null;
  let oldNo: number | null = null;
  let newNo: number | null = null;
  const push = (tone: DiffLineTone, text: string, o: number | null, n: number | null) => {
    // Header noise before the first +++ has no bucket; orphan content lines on
    // garbage input still surface, in one "(unparseable diff)" meta bucket.
    if (!current) {
      if (tone !== "meta") {
        current = { path: "(unparseable diff)", lines: [] };
        files.push(current);
      } else {
        return;
      }
    }
    current.lines.push({ tone, text, oldNo: o, newNo: n });
  };

  for (const raw of diffText.split(/\r?\n/)) {
    if (raw.startsWith("--- ") || raw.startsWith("+++ ")) {
      const path = raw.startsWith("+++ ")
        ? raw.slice(4).replace(/^b\//, "").trim()
        : raw.slice(4).replace(/^a\//, "").trim();
      if (raw.startsWith("+++ ")) {
        current = { path: path || "(unknown file)", lines: [] };
        files.push(current);
      }
      continue;
    }
    if (raw.startsWith("@@")) {
      const m = /@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw);
      oldNo = m ? Number(m[1]) : null;
      newNo = m ? Number(m[2]) : null;
      push("meta", raw, null, null);
      continue;
    }
    if (raw.startsWith("diff --git") || raw.startsWith("index ") || raw.startsWith("new file mode") || raw.startsWith("deleted file mode") || raw.startsWith("similarity ") || raw.startsWith("rename ") || raw.startsWith("Binary files")) {
      push("meta", raw, null, null);
      continue;
    }
    if (raw.startsWith("+")) {
      push("add", raw.slice(1), null, newNo);
      if (newNo !== null) newNo += 1;
      continue;
    }
    if (raw.startsWith("-")) {
      push("del", raw.slice(1), oldNo, null);
      if (oldNo !== null) oldNo += 1;
      continue;
    }
    push("context", raw.replace(/^ /, ""), oldNo, newNo);
    if (oldNo !== null) oldNo += 1;
    if (newNo !== null) newNo += 1;
  }
  return files;
}

/** Changed-file summary from the review DTO, or null when nothing changed. */
export interface DiffFileStat {
  path: string;
  additions: number;
  deletions: number;
  status: string;
}

/** "+7 −1" style summary for one file. */
export function fileStatLabel(f: DiffFileStat): string {
  const parts: string[] = [];
  if (f.additions > 0) parts.push(`+${f.additions}`);
  if (f.deletions > 0) parts.push(`−${f.deletions}`);
  return parts.length > 0 ? parts.join(" ") : "no line changes";
}
