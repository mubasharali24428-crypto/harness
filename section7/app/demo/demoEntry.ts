// M19-N4 — shared constants + pure helpers for the canned-demo entry.
// Lives OUTSIDE app/ because Next route modules may export ONLY the default
// (route-module law, W4 carryover) — the page imports from here instead.

/** Query carried to the run detail page so it can show the honest replay
 *  banner. Purely a UI marker; the backend has no notion of it. */
export const DEMO_LANDING_QUERY = "demo=1";

export type DemoSeedPick = { runId: string; projectId: string };

/** Pure: the landing href for a seeded run, carrying the demo marker query. */
export function demoLandingHref(seeded: DemoSeedPick): string {
  return `/projects/${encodeURIComponent(seeded.projectId)}/runs/${encodeURIComponent(
    seeded.runId,
  )}?${DEMO_LANDING_QUERY}`;
}
