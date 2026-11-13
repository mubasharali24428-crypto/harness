// U0: one list feeds the command palette, the "g then letter" shortcuts and their on-screen hints,
// so a shortcut can never exist without being shown (and the reverse).

export interface Command {
  id: string;
  label: string;
  href: string;
  /** Second key of the "g then <key>" sequence. */
  key: string;
  keywords?: string;
}

export const NAV_COMMANDS: Command[] = [
  { id: "home", label: "Command Center", href: "/", key: "h", keywords: "home start" },
  { id: "runs", label: "Runs", href: "/runs", key: "r", keywords: "agents tasks" },
  { id: "review", label: "Review", href: "/review", key: "v", keywords: "diff approve" },
  { id: "meeting", label: "Meeting", href: "/meeting", key: "m", keywords: "plan discuss" },
  { id: "cost", label: "Cost", href: "/dashboard/cost", key: "c", keywords: "spend budget ledger" },
  { id: "notifications", label: "Notifications", href: "/notifications", key: "n", keywords: "inbox unread" },
  { id: "settings", label: "Settings", href: "/settings", key: "s", keywords: "team keys models" },
  { id: "onboarding", label: "Get started", href: "/onboarding", key: "o", keywords: "setup checklist" },
];

/** True when a key press belongs to the page, not to something the user is typing into. */
export function isTypingTarget(tag: string | undefined, editable: boolean): boolean {
  return editable || ["INPUT", "TEXTAREA", "SELECT"].includes((tag ?? "").toUpperCase());
}

/**
 * The "g then letter" state machine as a pure step: given whether "g" is pending and the key just
 * pressed, return the route to open (if any) and whether "g" is pending afterwards.
 */
export function stepShortcut(
  pending: boolean,
  key: string,
  commands: Command[] = NAV_COMMANDS,
): { href: string | null; pending: boolean } {
  const k = key.toLowerCase();
  if (!pending) return { href: null, pending: k === "g" };
  return { href: commands.find((c) => c.key === k)?.href ?? null, pending: false };
}
