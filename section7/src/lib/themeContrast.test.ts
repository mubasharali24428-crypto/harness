// M-A3 — contrast regression guard.
//
// The light-theme audit measured the Settings hub links at 1.76:1 and the plan
// gate's "no-plan" pill at 2.54:1 because pill/anchor colours were dark-theme
// hex literals rather than theme tokens. This test pins the two things that
// let that happen: (1) every token pair the UI actually renders must clear
// WCAG AA in BOTH themes, and (2) the shared surfaces must consume var()
// tokens, never raw hex.
//
// Node-env test (no jsdom in this repo): it reads the stylesheet as text, so it
// runs in the same lane as the other pure frontend tests.
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const css = readFileSync(join(__dirname, "../../app/globals.css"), "utf8");

/** Parse one `:root`-style block into a token map. */
function tokens(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/(--[\w-]+):\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

const darkBlock = css.slice(css.indexOf(":root {"), css.indexOf(':root[data-theme="light"]'));
const lightBlock = css.slice(css.indexOf(':root[data-theme="light"]'));
const DARK = tokens(darkBlock);
const LIGHT = tokens(lightBlock);

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)) as [number, number, number];
}

function luminance([r, g, b]: [number, number, number]): number {
  const f = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

/** WCAG contrast ratio between two #rrggbb colours. */
export function contrast(a: string, b: string): number {
  const l1 = luminance(hexToRgb(a));
  const l2 = luminance(hexToRgb(b));
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

// Pairs the UI actually renders: foreground token on the surface it sits on.
const PAIRS: Array<{ name: string; fg: string; bg: string; min: number }> = [
  { name: "body text", fg: "--text", bg: "--bg", min: 4.5 },
  { name: "secondary text on panel", fg: "--text-2", bg: "--panel", min: 4.5 },
  { name: "muted text on panel", fg: "--muted", bg: "--panel", min: 4.5 },
  { name: "muted label on sidebar", fg: "--faint", bg: "--sidebar", min: 4.5 },
  { name: "link / accent on panel", fg: "--accent", bg: "--panel", min: 4.5 },
  { name: "success pill on panel", fg: "--green", bg: "--panel", min: 4.5 },
  { name: "warning pill on panel", fg: "--yellow", bg: "--panel", min: 4.5 },
  { name: "danger pill on panel", fg: "--red", bg: "--panel", min: 4.5 },
  { name: "info pill on panel", fg: "--blue", bg: "--panel", min: 4.5 },
  { name: "reviewing pill on panel", fg: "--violet", bg: "--panel", min: 4.5 },
  { name: "pr-opened pill on panel", fg: "--teal", bg: "--panel", min: 4.5 },
  { name: "input text on input", fg: "--text", bg: "--input", min: 4.5 },
];

for (const [theme, map] of [
  ["dark", DARK],
  ["light", LIGHT],
] as const) {
  describe(`${theme} theme token palette`, () => {
    for (const pair of PAIRS) {
      it(`${pair.name} clears WCAG AA`, () => {
        const fg = map[pair.fg];
        const bg = map[pair.bg];
        expect(fg, `missing token ${pair.fg}`).toBeTruthy();
        expect(bg, `missing token ${pair.bg}`).toBeTruthy();
        expect(contrast(fg, bg)).toBeGreaterThanOrEqual(pair.min);
      });
    }
  });
}

describe("shared surfaces use tokens, not theme-specific literals", () => {
  it("status pills take their text colour from a variable", () => {
    const pillRules = css.slice(css.indexOf(".pill.green"), css.indexOf(".event-feed"));
    expect(pillRules).toContain("var(--green)");
    expect(pillRules).toContain("var(--yellow)");
    expect(pillRules).not.toMatch(/\.pill\.\w+\s*\{[^}]*color:\s*#/);
  });

  it("anchors are coloured by the accent token", () => {
    const anchorRule = css.slice(css.indexOf("a[href] {"), css.indexOf("a[href] {") + 260);
    expect(anchorRule).toContain("color: var(--accent)");
  });
});

describe("run surface components stay token-driven", () => {
  const sources: Array<[string, string]> = [
    ["StatusPanel", "../../src/components/StatusPanel.tsx"],
    ["CheckpointsPanel", "../../src/components/CheckpointsPanel.tsx"],
    ["planGate", "../../app/dashboard/planGate.tsx"],
  ];
  for (const [name, rel] of sources) {
    it(`${name} hardcodes no status colour`, () => {
      const src = readFileSync(join(__dirname, rel), "utf8");
      // #8ee5a1 / #a8a296 / #6cb8ff / #e5a814 are dark-theme values; on paper
      // they fall under AA, which is exactly how the audit found them.
      expect(src).not.toMatch(/#(8ee5a1|a8a296|6cb8ff|e5a814|c9a7ff|6cd5c0|93c5fd)/i);
    });
  }
});
