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
