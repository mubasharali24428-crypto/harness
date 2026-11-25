// R4: what the Diff pane shows. "since-last" is the newest checkpoint's hunks
// (the default whenever one exists); "full-pr" is the pull request's whole
// diff plus the snapshot history (ChangeReview). Pure decisions only.

export type DiffScope = "since-last" | "full-pr";

export const DIFF_SCOPES: ReadonlyArray<{ id: DiffScope; label: string }> = [
  { id: "since-last", label: "Since last iteration" },
  { id: "full-pr", label: "Full pull request" },
];

/** Since-last is the default whenever there is an iteration to show. */
export function defaultDiffScope(hasIteration: boolean): DiffScope {
  return hasIteration ? "since-last" : "full-pr";
}

/**
 * The snapshot history (ChangeReview) under the Diff pane repeats the
 * since-last hunks, so it shows only on the full-PR scope. While the scope is
 * still being decided (null) it stays hidden, so it never flashes in and out.
 */
export function showChangeReview(pane: string | null, scope: DiffScope | null): boolean {
  return pane !== "diff" || scope === "full-pr";
}

// X-07 blind-approval KPI × R4 (merge of master into PR 27, 2026-11-25).
// Rule applied (owner-confirmable, docs/decisions/2026-11-25-blind-approval-diff-seen-DRAFT.md):
// "diff seen" counts only when the full pull request diff was on screen, or
// when the since-last view covered every hunk of the full pull request. A
// reviewer who saw only the newest hunks has not seen the whole change.

/** Minimal shape of a since-last file (CheckpointFile from the sandbox). */
export interface IterationFileLike {
  path: string;
  binary: boolean;
  hunks: ReadonlyArray<{ lines: readonly string[] }>;
}

function changedLines(lines: readonly string[]): string {
  return lines
    .filter((l) => (l.startsWith("+") && !l.startsWith("+++")) || (l.startsWith("-") && !l.startsWith("---")))
    .join("\n");
}

/** Every file and hunk of the PR's unified diff: path -> list of changed-line keys. */
function prHunks(prDiff: string): Map<string, string[]> {
  const out = new Map<string, string[]>();
  let path: string | null = null;
  let oldPath: string | null = null;
  let hunk: string[] | null = null;
  const flush = () => {
    if (path !== null && hunk !== null) out.get(path)!.push(changedLines(hunk));
    hunk = null;
  };
  for (const raw of prDiff.split(/\r?\n/)) {
    if (raw.startsWith("diff --git")) {
      flush();
      path = null;
      continue;
    }
    if (raw.startsWith("--- ")) {
      flush();
      const p = raw.slice(4).replace(/^a\//, "").trim();
      oldPath = p === "/dev/null" ? null : p;
      continue;
    }
    if (raw.startsWith("+++ ")) {
      flush();
      const p = raw.slice(4).replace(/^b\//, "").trim();
      // A deleted file names itself only on the --- line.
      path = p === "/dev/null" ? oldPath : p;
      if (path !== null && !out.has(path)) out.set(path, []);
      continue;
    }
    if (raw.startsWith("Binary files ")) {
      // "Binary files a/x and b/x differ": the file exists with no textual hunk.
      const m = / b\/(.+) differ$/.exec(raw) ?? / a\/(.+) and \/dev\/null differ$/.exec(raw);
      if (m && !out.has(m[1])) out.set(m[1], []);
      continue;
    }
    if (raw.startsWith("@@")) {
      flush();
      hunk = [];
      continue;
    }
    if (hunk !== null) hunk.push(raw);
  }
  flush();
  return out;
}

/**
 * True when the since-last view shows the whole change: every file of the
 * full PR diff is in it, and every PR hunk's added/removed lines appear as a
 * hunk of the same file. An empty or missing PR diff never counts (nothing to
 * compare against, so it is not proof).
 */
export function sinceLastCoversFullPr(
  prDiff: string | null | undefined,
  files: readonly IterationFileLike[] | null | undefined,
): boolean {
  if (!prDiff || !files || files.length === 0) return false;
  const pr = prHunks(prDiff);
  if (pr.size === 0) return false;
  const byPath = new Map<string, IterationFileLike>(files.map((f) => [f.path, f]));
  for (const [path, hunks] of pr) {
    const f = byPath.get(path);
    if (!f) return false;
    if (hunks.length === 0) {
      if (!f.binary && f.hunks.length === 0) return false;
      continue;
    }
    const have = new Set(f.hunks.map((h) => changedLines(h.lines)));
    if (!hunks.every((h) => have.has(h))) return false;
  }
  return true;
}

/** Does the diff on screen count as "diff seen" for the blind-approval KPI? */
export function diffViewCountsAsSeen(scope: DiffScope | null, sinceLastCoversAll: boolean): boolean {
  if (scope === "full-pr") return true;
  return scope === "since-last" && sinceLastCoversAll;
}
