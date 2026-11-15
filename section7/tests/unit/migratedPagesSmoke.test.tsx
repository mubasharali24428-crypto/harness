// M16-X4 (raw-17) — render smoke coverage for the four pages migrated onto
// the DS primitives (login, meeting, review, dashboard root), plus the lane's
// ZERO-inline-style audit: every surviving `style={{…}}` must match one of the
// DOCUMENTED one-off exceptions from the M16-X4 report.
//
// Repo protocol: node-env vitest (no jsdom/RTL) → react-dom/server renders
// the REAL pages and assertions run over emitted markup (same technique as
// tests/unit/a11yAccessibleNames.test.tsx and meetingRestore.test.tsx).
import { describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

// AppShell is shared chrome outside this lane's owned paths and pulls the
// Next router; swap it for a pass-through (same trick as the other suites).
vi.mock("@/components/AppShell", () => ({
  default: (props: { children?: React.ReactNode }) =>
    React.createElement("div", null, props.children),
}));

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = join(HERE, "..", "..", "app");

/** Extract every inline style object literal from a page's source. */
function inlineStyles(src: string): string[] {
  const out: string[] = [];
  const re = /style=\{\{([\s\S]*?)\}\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src)) !== null) out.push(m[1]);
  return out;
}

/** Every survivor must carry one of the documented exception signatures. */
function expectOnlyDocumentedExceptions(src: string, allowed: RegExp[]): void {
  const styles = inlineStyles(src);
  for (const block of styles) {
    const ok = allowed.some((re) => re.test(block));
    if (!ok) {
      throw new Error(
        `Undocumented inline style survived migration: {${block.trim().slice(0, 120)}…}`,
      );
    }
  }
}

/* ------------------------------------------------------------------ *
 * Login                                                              *
 * ------------------------------------------------------------------ */

describe("login page DS migration (M16-X4)", () => {
  const ALLOWED = [
    /display: "grid", placeItems: "center", minHeight: "100vh"/, // page-root centering wrapper
    /width: "100%"/, // Button full-width form controls (no fullWidth prop in frozen contract)
    /marginTop: 6/, // ← Back button spacing nudge
    /marginTop: 8/, // Notice error-line spacing nudge
    /letterSpacing: 8/, // OTP input digits (native input — outside ui/ scope)
  ];

  it("renders the sign-in card through the primitives with legacy copy intact", async () => {
    const { default: Login } = await import("../../app/login/page");
    const html = renderToStaticMarkup(React.createElement(Login));

    expect(html).toContain('class="card login-card"'); // card shell kept
    expect(html).toContain("Sign in to your team workspace");
    // Brand wordmark line (accent ink + wide tracking — the .brand face):
    expect(html).toMatch(/<h2 style="color:var\(--accent\);letter-spacing:2px">HARNESS<\/h2>/);
    // DS Button face + full-width form control + primary variant:
    expect(html).toMatch(/<button[^>]*type="submit"[^>]*>/);
    expect(html).toContain("Enter the harness");
    expect(html).toContain("width:100%");
  });

  it("zero undocumented inline styles remain (exception audit)", async () => {
    const src = readFileSync(join(APP, "login", "page.tsx"), "utf8");
    expectOnlyDocumentedExceptions(src, ALLOWED);
  });
});

/* ------------------------------------------------------------------ *
 * Meeting                                                            *
 * ------------------------------------------------------------------ */

describe("meeting page DS migration (M16-X4)", () => {
  const ALLOWED = [/marginLeft: 8/]; // Approve-brief spacing nudge

  it("renders chat + brief cards through the primitives with e2e hooks intact", async () => {
    const { default: Meeting } = await import("../../app/meeting/page");
    const html = renderToStaticMarkup(React.createElement(Meeting));

    expect(html).toContain("Meeting Area");
    // Pill keeps the .pill class hooks the Playwright journey keys on:
    expect(html).toMatch(/class="pill yellow"/); // idle state
    // Card(as="div") keeps .card.chat flex column + .grid child semantics,
    // with the DS panel face underneath:
    expect(html).toContain('class="card chat"');
    expect(html).toContain("var(--panel)"); // Card surface from the primitive
    expect(html).toContain("Brief lifecycle");
    expect(html).toContain("Start a live meeting");
  });

  it("zero undocumented inline styles remain (exception audit)", async () => {
    const src = readFileSync(join(APP, "meeting", "page.tsx"), "utf8");
    expectOnlyDocumentedExceptions(src, ALLOWED);
  });
});

/* ------------------------------------------------------------------ *
 * Review                                                             *
 * ------------------------------------------------------------------ */

describe("review page DS migration (M16-X4)", () => {
  const ALLOWED = [
    /gridColumn: "1 \/ -1"/, // summary card spans the grid row
    /display: "flex", gap: 8/, // human-gate action row
  ];

  it("renders the honest empty state with Pill hooks intact", async () => {
    const { default: Review } = await import("../../app/review/page");
    const html = renderToStaticMarkup(React.createElement(Review));

    expect(html).toContain("Review Area");
    expect(html).toMatch(/class="pill yellow"/); // "no PR yet" header pill
    // PrEmptyState still owns the empty state (its own card, untouched here):
    expect(html).toContain("No pull request yet");
  });

  it("zero undocumented inline styles remain (exception audit)", async () => {
    const src = readFileSync(join(APP, "review", "page.tsx"), "utf8");
    expectOnlyDocumentedExceptions(src, ALLOWED);
  });
});

/* ------------------------------------------------------------------ *
 * Dashboard root                                                     *
 * ------------------------------------------------------------------ */

describe("dashboard root DS migration (M16-X4)", () => {
  const ALLOWED = [/color: "var\(--accent\)"/]; // Go-to-login link ink

  it("serves the landing page to signed-out visitors, with both entry points", async () => {
    const { default: CommandCenter } = await import("../../app/page");
    const html = renderToStaticMarkup(React.createElement(CommandCenter));

    expect(html).toContain("Keep the veto.");
    expect(html).toContain('href="/demo"');
    expect(html).toContain('href="/login"');
    expect(html).not.toContain("Command Center");
  });

  it("zero undocumented inline styles remain (exception audit)", async () => {
    const src = readFileSync(join(APP, "page.tsx"), "utf8");
    expectOnlyDocumentedExceptions(src, ALLOWED);
  });
});
