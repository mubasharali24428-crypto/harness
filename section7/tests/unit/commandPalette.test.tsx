import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { CommandPaletteView } from "../../src/components/CommandPalette";
import { type Command, matchCommands, matchesShortcut, parseShortcut, validateCommands } from "../../src/lib/commandPalette";

const c = (id: string, title: string, shortcut?: string, keywords?: string[]): Command => ({ id, title, shortcut, keywords, run: () => {} });
const cmds = [c("review", "Open review", "mod+shift+r"), c("inbox", "Open task inbox", "g", ["queue"]), c("status", "Service status")];

describe("command palette model", () => {
  it("ranks exact > prefix > word-start > substring > subsequence and honours keywords", () => {
    expect(matchCommands(cmds, "open review")[0].id).toBe("review");
    expect(matchCommands(cmds, "ser")[0].id).toBe("status");
    expect(matchCommands(cmds, "queue").map((x) => x.id)).toEqual(["inbox"]);
    expect(matchCommands(cmds, "orv").map((x) => x.id)).toContain("review"); // subsequence
    expect(matchCommands(cmds, "zzz")).toEqual([]);
    expect(matchCommands(cmds, "  ")).toEqual(cmds);
  });
  it("parses and matches chords; Ctrl or Cmd both satisfy mod; extra modifiers do not", () => {
    expect(parseShortcut("mod+shift+p")).toEqual({ mod: true, shift: true, alt: false, key: "p" });
    expect(parseShortcut("hyper+p")).toBeNull();
    expect(parseShortcut("mod+mod+p")).toBeNull();
    const ev = (o: object) => ({ key: "k", ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...o });
    expect(matchesShortcut(ev({ ctrlKey: true }), "mod+k")).toBe(true);
    expect(matchesShortcut(ev({ metaKey: true }), "mod+k")).toBe(true);
    expect(matchesShortcut(ev({ ctrlKey: true, shiftKey: true }), "mod+k")).toBe(false);
    expect(matchesShortcut(ev({}), "mod+k")).toBe(false);
    expect(matchesShortcut(ev({ key: "K", ctrlKey: true }), "mod+k")).toBe(true);
  });
  it("flags duplicate ids, invalid, reserved and conflicting shortcuts", () => {
    expect(validateCommands(cmds)).toEqual([]);
    const bad = [c("a", "A", "mod+w"), c("b", "B", "mod+j"), c("c", "C", "mod+j"), c("a", "D"), c("e", "E", "bogus+x")];
    const problems = validateCommands(bad).join("|");
    expect(problems).toMatch(/reserved/);
    expect(problems).toMatch(/conflicts with b/);
    expect(problems).toMatch(/duplicate id a/);
    expect(problems).toMatch(/invalid shortcut/);
  });
});

describe("CommandPaletteView a11y structure", () => {
  const html = renderToStaticMarkup(
    <CommandPaletteView commands={cmds} query="" active={1} onQuery={() => {}} onActive={() => {}} onRun={() => {}} onClose={() => {}} />,
  );
  it("uses dialog + combobox + listbox with the active option referenced", () => {
    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    expect(html).toContain('role="combobox"');
    expect(html).toContain('role="listbox"');
    expect(html).toMatch(/aria-activedescendant="[^"]*-inbox"/);
    expect(html.match(/aria-selected="true"/g)).toHaveLength(1);
  });
  it("shows an empty state when nothing matches", () => {
    const empty = renderToStaticMarkup(
      <CommandPaletteView commands={cmds} query="zzz" active={0} onQuery={() => {}} onActive={() => {}} onRun={() => {}} onClose={() => {}} />,
    );
    expect(empty).toContain("No matching commands");
  });
});
