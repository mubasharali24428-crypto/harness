// R2: the right pane shows one thing at a time. The pane lives in ?pane= so a
// reload, a shared link and back/forward land on the same view. Links written
// for the old five tabs (?tab=) keep working.

export type PaneId = "diff" | "plan" | "terminal" | "evidence" | "advanced";

export const PANES: ReadonlyArray<{ id: PaneId; label: string }> = [
  { id: "diff", label: "Diff" },
  { id: "plan", label: "Plan" },
  { id: "terminal", label: "Terminal" },
  { id: "evidence", label: "Evidence" },
  { id: "advanced", label: "Advanced" },
];

const OLD_TABS: Record<string, PaneId | null> = {
  overview: null,
  plan: "plan",
  team: "advanced",
  working: "terminal",
  changes: "diff",
};

export function paneFromSearch(search: string): PaneId | null {
  const params = new URLSearchParams(search);
  const pane = params.get("pane");
  if (PANES.some((p) => p.id === pane)) return pane as PaneId;
  return OLD_TABS[params.get("tab") ?? ""] ?? null;
}

/** The same URL with the pane set (or cleared) and the old ?tab= dropped. */
export function searchWithPane(search: string, pane: PaneId | null): string {
  const params = new URLSearchParams(search);
  params.delete("tab");
  if (pane) params.set("pane", pane);
  else params.delete("pane");
  const out = params.toString();
  return out ? `?${out}` : "";
}
