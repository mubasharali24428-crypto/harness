// Operability slice 5: what the command palette lists, as a pure function of the viewer's role,
// so the rules are tested without cmdk or a browser.
//
// - Routes use the same labels as the sidebar menu and the settings nav (one list, lib/commands).
// - An entry with a "g then <key>" shortcut carries it, so the palette shows it.
// - Role-gated actions appear only for a role that can do them. An unknown or missing role gets
//   no gated action (least privilege) but still gets every route: the server decides what a page
//   shows, the palette only gets a person there.
import { NAV_COMMANDS, OTHER_PAGES, SETTINGS_PAGES, shortcutText } from "./commands";

export type KnownRole = "admin" | "engineer" | "viewer" | "guest";

export interface PaletteEntry {
  id: string;
  label: string;
  /** Text cmdk filters on: label plus keywords. */
  value: string;
  href?: string;
  /** Keys of the "g then <key>" sequence, e.g. ["g", "r"]. */
  keys?: string[];
  /** Words for the accessible name, e.g. "g then r". */
  shortcut?: string;
  /** Non-navigation entries the palette runs itself. */
  run?: "theme" | "signout" | { project: string };
  note?: string;
}

export interface PaletteGroup {
  heading: string;
  entries: PaletteEntry[];
}

/** Actions a role may take, mirroring the server's own checks (roleCanAct: admin and engineer
 *  decide plans and approvals; inviting is admin-only on the identity service). */
export const PALETTE_ACTIONS: ReadonlyArray<{ id: string; label: string; href: string; roles: KnownRole[]; keywords: string }> = [
  { id: "action-decide", label: "Decide pending approvals", href: "/triage", roles: ["admin", "engineer"], keywords: "approve deny plan pending" },
  { id: "action-invite", label: "Invite a teammate", href: "/settings/team", roles: ["admin"], keywords: "team member add email invite" },
];

export function roleMay(role: string | null | undefined, roles: readonly KnownRole[]): boolean {
  return typeof role === "string" && (roles as readonly string[]).includes(role);
}

export interface PaletteOptions {
  workingHref?: string | null;
  projects?: Array<{ id: string; name: string }>;
  selectedProject?: string | null;
  canSwitchProject?: boolean;
  canSignOut?: boolean;
}

export function paletteGroups(role: string | null | undefined, o: PaletteOptions = {}): PaletteGroup[] {
  const goTo: PaletteEntry[] = [
    ...(o.workingHref ? [{ id: "working", label: "Working (current run)", value: "Working current run", href: o.workingHref }] : []),
    ...NAV_COMMANDS.map((c) => ({
      id: c.id,
      label: c.label,
      value: `${c.label} ${c.keywords ?? ""}`.trim(),
      href: c.href,
      keys: ["g", c.key],
      shortcut: shortcutText(c),
    })),
  ];
  const pages = (list: typeof SETTINGS_PAGES): PaletteEntry[] =>
    list.map((c) => ({ id: c.id, label: c.label, value: `${c.label} ${c.keywords ?? ""}`.trim(), href: c.href }));

  const actions: PaletteEntry[] = [
    ...PALETTE_ACTIONS.filter((a) => roleMay(role, a.roles)).map((a) => ({
      id: a.id, label: a.label, value: `${a.label} ${a.keywords}`, href: a.href,
    })),
    { id: "theme", label: "Switch light / dark theme", value: "Switch theme light dark appearance", run: "theme" as const },
    ...(o.canSignOut ? [{ id: "signout", label: "Sign out", value: "Sign out log out", run: "signout" as const }] : []),
  ];

  const groups: PaletteGroup[] = [
    { heading: "Go to", entries: goTo },
    { heading: "Settings", entries: pages(SETTINGS_PAGES) },
    { heading: "More pages", entries: pages(OTHER_PAGES) },
  ];
  const projects = o.projects ?? [];
  if (projects.length > 1 && o.canSwitchProject) {
    groups.push({
      heading: "Switch project",
      entries: projects.map((p) => ({
        id: `project-${p.id}`, label: p.name, value: `project ${p.name} ${p.id}`, run: { project: p.id },
        note: p.id === o.selectedProject ? "current" : undefined,
      })),
    });
  }
  groups.push({ heading: "Actions", entries: actions });
  return groups;
}
