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
  { id: "home", label: "New run", href: "/", key: "h", keywords: "home start goal" },
  { id: "runs", label: "Runs", href: "/runs", key: "r", keywords: "agents tasks" },
  { id: "review", label: "Review", href: "/review", key: "v", keywords: "diff approve" },
  { id: "meeting", label: "Meeting", href: "/meeting", key: "m", keywords: "plan discuss" },
  { id: "cost", label: "Cost", href: "/dashboard/cost", key: "c", keywords: "spend budget ledger" },
  { id: "notifications", label: "Notifications", href: "/notifications", key: "n", keywords: "inbox unread" },
  { id: "settings", label: "Settings", href: "/settings", key: "s", keywords: "team keys models" },
  { id: "onboarding", label: "Get started", href: "/onboarding", key: "o", keywords: "setup checklist" },
];

/** Pages the palette reaches without a shortcut: every settings section and the remaining routes. */
export const SETTINGS_PAGES: Array<Pick<Command, "id" | "label" | "href" | "keywords">> = [
  { id: "settings-project", label: "Project rules", href: "/settings/project", keywords: "settings conventions" },
  { id: "settings-team", label: "Team and invites", href: "/settings/team", keywords: "settings members roles" },
  { id: "settings-governance", label: "Organization governance", href: "/settings/governance", keywords: "settings policy" },
  { id: "settings-notifications", label: "Notification settings", href: "/settings/notifications", keywords: "email quiet hours" },
  { id: "settings-models", label: "Models and reasoning", href: "/settings/models", keywords: "settings effort" },
  { id: "settings-keys", label: "API keys", href: "/settings/keys", keywords: "settings providers byo" },
  { id: "settings-integrations", label: "Integrations and webhooks", href: "/settings/integrations", keywords: "settings github" },
  { id: "settings-billing", label: "Billing and plan", href: "/settings/billing", keywords: "settings invoice" },
  { id: "settings-account", label: "Your account", href: "/settings/account", keywords: "settings erase" },
];

export const OTHER_PAGES: Array<Pick<Command, "id" | "label" | "href" | "keywords">> = [
  { id: "triage", label: "Phone triage", href: "/triage", keywords: "approve deny pending mobile" },
  { id: "demo", label: "Demo", href: "/demo", keywords: "replay sample" },
  { id: "docs", label: "Docs", href: "/docs", keywords: "help reference" },
  { id: "pricing", label: "Pricing", href: "/pricing", keywords: "plans" },
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
