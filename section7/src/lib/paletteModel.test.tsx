// Operability slice 5: command palette. Node-env vitest (no jsdom/RTL in this repo). The pure model
// is tested directly; the palette's item markup is rendered through PaletteGroups with plain
// wrappers in place of cmdk's Group/Item (cmdk itself needs a DOM), so the labels, shortcut hints
// and accessible text asserted here are the ones the dialog renders.
import { describe, expect, it, vi } from "vitest";
import * as React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expand, findAll, press, textOf } from "../../tests/support/elementTree";
import { MENU, NAV_COMMANDS, SETTINGS_PAGES, shortcutText, stepShortcut } from "./commands";
import { PALETTE_ACTIONS, paletteGroups, roleMay, type PaletteEntry } from "./paletteModel";
import { TOUR_KEYS } from "@/components/KeyTour";
import { PaletteGroups } from "@/components/CommandPalette";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const all = (role: string | null | undefined, o = {}) => paletteGroups(role, o).flatMap((g) => g.entries);
const byId = (role: string | null | undefined, id: string) => all(role).find((e) => e.id === id);

const Group = ({ heading, children }: { heading: string; children: React.ReactNode }) =>
  React.createElement("div", { role: "group", "aria-label": heading }, children);
const Item = ({ children, onSelect, value, ...rest }: { children: React.ReactNode; onSelect: () => void; value: string }) =>
  React.createElement("div", { role: "option", "data-value": value, onClick: onSelect, ...rest }, children);
const render = (role: string | null, onSelect = vi.fn()) => ({
  onSelect,
  nodes: expand(React.createElement(PaletteGroups, { groups: paletteGroups(role), item: "", group: "", onSelect, Group, Item })),
});

describe("palette labels match the nav", () => {
  it("home, runs, review, cost and settings have one label each, shared with the sidebar menu", () => {
    const labels = Object.fromEntries(all("admin").map((e) => [e.id, e.label]));
    expect({ home: labels.home, runs: labels.runs, review: labels.review, cost: labels.cost, settings: labels.settings })
      .toEqual({ home: "New run", runs: "All runs", review: "Review", cost: "Cost", settings: "Settings" });
    for (const m of MENU) {
      const entry = all("admin").find((e) => e.href === m.href);
      expect(entry?.label, m.href).toBe(m.label);
    }
  });

  it("the sidebar's New run link and the key tour use the same words as the palette", () => {
    const shell = readFileSync(join(__dirname, "../components/AppShell.tsx"), "utf8");
    expect(shell).toMatch(/>\s*New run\s*</);
    expect(shell).not.toMatch(/label: "All runs"/); // the menu no longer keeps its own copy of the labels
    for (const k of TOUR_KEYS.filter((t) => t.keys[0] === "g")) {
      const href = stepShortcut(true, k.keys[1]).href;
      expect(NAV_COMMANDS.find((c) => c.href === href)?.label).toBe(k.does);
    }
  });

  it("team uses the settings nav's label (same list)", () => {
    const team = SETTINGS_PAGES.find((p) => p.id === "settings-team")!;
    expect(byId("viewer", "settings-team")?.label).toBe(team.label);
    expect(byId("viewer", "settings-team")?.href).toBe("/settings/team");
  });
});

describe("shortcuts are shown when they exist", () => {
  it("every go-to route carries its g-shortcut; pages without one carry none", () => {
    for (const c of NAV_COMMANDS) {
      const e = byId("admin", c.id)!;
      expect(e.keys).toEqual(["g", c.key]);
      expect(e.shortcut).toBe(shortcutText(c));
    }
    expect(byId("admin", "settings-team")?.keys).toBeUndefined();
  });

  it("the rendered item shows the keys and names the shortcut in words", () => {
    const { nodes } = render("admin");
    const runs = findAll(nodes, (n) => n.props["data-entry"] === "runs")[0];
    expect(findAll([runs], (n) => n.type === "kbd").map(textOf)).toEqual(["g", "r"]);
    expect(textOf(runs)).toContain("All runs, shortcut g then r");
  });

  it("selecting an item hands its entry back (the dialog navigates to its href)", () => {
    const { nodes, onSelect } = render("viewer");
    press(findAll(nodes, (n) => n.props["data-entry"] === "cost")[0]);
    expect((onSelect.mock.calls[0][0] as PaletteEntry).href).toBe("/dashboard/cost");
  });
});

describe("role-gated actions", () => {
  it("roleMay is exact and refuses unknown roles", () => {
    expect(roleMay("admin", ["admin"])).toBe(true);
    expect(roleMay("viewer", ["admin", "engineer"])).toBe(false);
    expect(roleMay(null, ["admin"])).toBe(false);
    expect(roleMay("superuser", ["admin"])).toBe(false);
  });

  it("an admin sees both actions; an engineer cannot invite; a viewer or guest sees neither", () => {
    const actions = (role: string) => all(role).filter((e) => e.id.startsWith("action-")).map((e) => e.id);
    expect(actions("admin")).toEqual(["action-decide", "action-invite"]);
    expect(actions("engineer")).toEqual(["action-decide"]);
    expect(actions("viewer")).toEqual([]);
    expect(actions("guest")).toEqual([]);
  });

  it("the rendered viewer palette has no Invite or Decide option at all", () => {
    const text = render("viewer").nodes.map(textOf).join(" ");
    for (const a of PALETTE_ACTIONS) expect(text).not.toContain(a.label);
  });

  it("an unknown or missing role gets no gated action but still every route", () => {
    for (const role of [null, undefined, "", "superuser"]) {
      const entries = all(role);
      expect(entries.some((e) => e.id.startsWith("action-")), String(role)).toBe(false);
      for (const c of [...NAV_COMMANDS, ...SETTINGS_PAGES]) {
        expect(entries.some((e) => e.href === c.href), `${String(role)} ${c.href}`).toBe(true);
      }
    }
  });

  it("theme is always there; sign out only when the shell can sign out", () => {
    expect(byId(null, "theme")).toBeTruthy();
    expect(all(null).some((e) => e.id === "signout")).toBe(false);
    expect(all(null, { canSignOut: true }).some((e) => e.id === "signout")).toBe(true);
  });
});
