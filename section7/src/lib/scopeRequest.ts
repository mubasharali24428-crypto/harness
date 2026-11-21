/** Pure helpers for the scope-request editor. Kept out of the app-dir
 *  container so node-env tests can exercise them without pulling in the
 *  intent client's module graph. */

/** The run's current scope paths plus the requested path, one per line, deduped. */
export function scopeEditorPathsText(current: string[] | null, requested: string): string {
  const lines = [...(current ?? [])];
  if (!lines.includes(requested)) lines.push(requested);
  return lines.join("\n");
}

/** One path per line, blanks dropped (same rule as the existing intent form). */
export function scopeEditorPaths(text: string): string[] {
  return text.split("\n").map((line) => line.trim()).filter(Boolean);
}

export type ScopeEditPut = (
  runId: string,
  token: string,
  version: number,
  patch: { scopePaths: string[] },
) => Promise<unknown>;

/** The ONLY path that writes the intent. `put` is injectable so a test can
 *  prove no PUT happens before the person confirms. */
export function submitScopeEdit(
  put: ScopeEditPut,
  args: { runId: string; token: string; version: number; pathsText: string },
): Promise<unknown> {
  return put(args.runId, args.token, args.version, {
    scopePaths: scopeEditorPaths(args.pathsText),
  });
}
