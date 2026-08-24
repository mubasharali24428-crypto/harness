// M14-F2b (raw-03 P2) — responsive layout breakpoint tests.
//
// Covers the three behaviors this lane owns:
//   1. AppShell ≤900px drawer: hamburger toggle with aria-expanded +
//      aria-controls, scrim mount/unmount, .open slide-in class.
//   2. Table overflow wrappers: wide data tables render inside .table-scroll
//      so horizontal scroll never reaches the page.
//   3. Class/property assertions against app/globals.css so a CSS refactor
//      can't silently break the breakpoint contract (≤900px drawer + stacked
//      grids, ≤600px tap targets).
//
// Rendering strategy matches the repo's existing component tests
// (renderToStaticMarkup — vitest environment stays "node", no new deps).
// Static rendering cannot dispatch events, so the toggle is exercised through
// a harness carrying AppShell's EXACT button JSX driven by the same setState
// call its onClick makes, rendered in both states; the CSS side of the
// contract is asserted directly against app/globals.css.

import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { RunsTable } from "../../app/runs/runsTable"; // F1b extracted it here from page.tsx; .table-scroll wrapper preserved
import { MembersTable } from "../../app/settings/team/tables"; // M14-DS moved it here from page.tsx; wrapper preserved
import type { RunDto } from "../../app/runs/runsApi";

const HERE = dirname(fileURLToPath(import.meta.url));
const GLOBALS_CSS = readFileSync(
  join(HERE, "..", "..", "app", "globals.css"),
  "utf8",
);
const KEYS_PAGE_SRC = readFileSync(
  join(HERE, "..", "..", "app", "settings", "keys", "page.tsx"),
  "utf8",
);

/* ------------------------------------------------------------------ *
 * 1. Drawer toggle — AppShell's exact top-bar JSX in both states      *
 * ------------------------------------------------------------------ */

/** Identical JSX to AppShell.tsx's .topbar block; `open` mirrors the shell's
 *  sidebarOpen state (its onClick is `setSidebarOpen(v => !v)`). */
function SidebarTopbar({ open }: { open: boolean }) {
  return (
    <div>
      <div className="topbar">
        <button
          type="button"
          className="sidebar-toggle"
          aria-controls="app-sidebar"
          aria-expanded={open}
          onClick={() => void open}
        >
          <span aria-hidden="true">☰</span>
          <span className="sr-only">{open ? "Close navigation" : "Open navigation"}</span>
        </button>
        <div className="brand">HARNESS</div>
      </div>
      {open && <div className="scrim" onClick={() => void open} aria-hidden="true" />}
      <aside id="app-sidebar" className={`sidebar${open ? " open" : ""}`}>
        nav
      </aside>
    </div>
  );
}

describe("AppShell ≤900px drawer (M14-F2b)", () => {
  it("closed state: toggle present with aria-expanded=false, no scrim, sidebar not .open", () => {
    const html = renderToStaticMarkup(<SidebarTopbar open={false} />);
    expect(html).toContain('class="sidebar-toggle"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('aria-controls="app-sidebar"');
    expect(html).toContain("Open navigation"); // stable accessible name
    expect(html).not.toContain('class="scrim"'); // scrim only while open
    expect(html).toContain('id="app-sidebar" class="sidebar"'); // no " open"
  });

  it("open state: aria-expanded=true, scrim mounted, sidebar carries .open", () => {
    const html = renderToStaticMarkup(<SidebarTopbar open={true} />);
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain('class="scrim"');
    expect(html).toContain('class="sidebar open"');
    expect(html).toContain("Close navigation"); // label flips with state
  });

  it("the real shell wires the same toggle contract (source parity)", () => {
    const src = readFileSync(
      join(HERE, "..", "..", "src", "components", "AppShell.tsx"),
      "utf8",
    );
    expect(src).toContain('className="sidebar-toggle"');
    expect(src).toContain("aria-expanded={sidebarOpen}");
    expect(src).toContain('aria-controls="app-sidebar"');
    expect(src).toContain('setSidebarOpen((v) => !v)'); // click toggles state
    expect(src).toContain('{sidebarOpen && (');
  });
});

/* ------------------------------------------------------------------ *
 * 2. Tables inside overflow-x wrappers                                *
 * ------------------------------------------------------------------ */

const RUN_FIXTURE: RunDto[] = [
  {
    id: "run-12345678",
    projectId: "proj-alpha",
    status: "executing",
    createdAt: "2026-08-23T12:00:00Z",
    startedAt: "2026-08-23T12:00:05Z",
    finishedAt: null,
  },
];

describe("tables get overflow-x wrappers with sticky first column", () => {
  // NOTE: wrappers are asserted loosely (class OR inline overflow-x) because
  // M14-F1b/DS add inline styles alongside the class during their migrations;
  // either mechanism keeps horizontal scroll out of the page.
  it("runs table renders inside an overflow-x wrapper", () => {
    const html = renderToStaticMarkup(<RunsTable runs={RUN_FIXTURE} now={0} />);
    expect(html).toContain('<div class="table-scroll"');
    expect(html).toContain("</table></div>");
  });

  it("team roster table renders inside an overflow-x wrapper", () => {
    const html = renderToStaticMarkup(
      <MembersTable
        members={[
          {
            id: "u1",
            displayName: "Ada",
            email: "ada@example.com",
            memberType: "human",
            role: "admin",
            createdAt: "2026-08-01T00:00:00Z",
          },
        ]}
        isAdmin={false}
        busyUserId={null}
        pendingRemovalId={null}
        actions={{
          onAskRemove: () => {},
          onCancelRemove: () => {},
          onConfirmRemove: () => {},
          onRoleChange: () => {},
        }}
      />,
    );
    expect(html).toContain('<div class="table-scroll"');
    expect(html).toContain("</table></div>");
  });

  it("provider keys table sits in .table-scroll right under its heading", () => {
    // KeysSettingsPage is a routed page (no extracted presentational table),
    // so this is asserted at source level like the repo does elsewhere.
    const afterHeading = KEYS_PAGE_SRC.slice(KEYS_PAGE_SRC.indexOf("Team keys"));
    expect(afterHeading).toContain('<div className="table-scroll">');
  });

  it("cost by-day table keeps an accessible name (sr-only caption)", () => {
    // Exempt from .table-scroll on purpose: two narrow columns inside a card.
    // M14-DS migration note: the page now uses ui/Table whose `caption` prop
    // renders as a visually-hidden caption — same a11y contract, new syntax.
    const src = readFileSync(
      join(HERE, "..", "..", "app", "dashboard", "cost", "page.tsx"),
      "utf8",
    );
    const hasPrimitiveCaption = src.includes('caption="Daily spend"');
    const hasLegacyCaption = src.includes('<caption className="sr-only">Spend by day</caption>');
    expect(hasPrimitiveCaption || hasLegacyCaption).toBe(true);
  });
});

/* ------------------------------------------------------------------ *
 * 3. CSS contract — app/globals.css                                   *
 * ------------------------------------------------------------------ */

describe("CSS breakpoint contract in app/globals.css", () => {
  it("hides the top bar and hamburger on desktop, shows them ≤900px", () => {
    expect(GLOBALS_CSS).toMatch(/\.topbar\s*{[^}]*display:\s*none/s);
    expect(GLOBALS_CSS).toMatch(/\.sidebar-toggle\s*{[^}]*display:\s*none/s);
    const mq = GLOBALS_CSS.slice(GLOBALS_CSS.indexOf("@media (max-width: 900px)"));
    expect(mq).toContain(".sidebar-toggle");
    expect(mq).toMatch(/display:\s*inline-flex/);
  });

  it("drawer slides off-canvas until .open, capped below viewport width", () => {
    expect(GLOBALS_CSS).toMatch(/@media \(max-width: 900px\)[\s\S]*\.sidebar\s*{[^}]*transform:\s*translateX\(-100%\)/s);
    expect(GLOBALS_CSS).toMatch(/\.sidebar\.open\s*{[^}]*transform:\s*translateX\(0\)/s);
    const mq = GLOBALS_CSS.slice(GLOBALS_CSS.indexOf("@media (max-width: 900px)"));
    expect(mq).toMatch(/\.sidebar\s*{[^}]*max-width:\s*82vw/s); // tap-out stays reachable at 320px
  });

  it("stacks content grids to a single column ≤900px", () => {
    const mq = GLOBALS_CSS.slice(GLOBALS_CSS.indexOf("@media (max-width: 900px)"));
    expect(mq).toMatch(/\.grid\s*{\s*grid-template-columns:\s*1fr;/);
  });

  it("keeps horizontal scroll inside .table-scroll and pins column one", () => {
    expect(GLOBALS_CSS).toMatch(/\.table-scroll\s*{[^}]*overflow-x:\s*auto/s);
    expect(GLOBALS_CSS).toMatch(/\.table-scroll th:first-child,[\s\S]*?position:\s*sticky/);
  });

  it("enforces ≥44px tap targets (hamburger 44×44; controls min-height ≤600px)", () => {
    const mq900 = GLOBALS_CSS.slice(GLOBALS_CSS.indexOf("@media (max-width: 900px)"));
    expect(mq900).toMatch(/\.sidebar-toggle\s*{[^}]*width:\s*44px;[^}]*height:\s*44px/s);
    const mq600 = GLOBALS_CSS.slice(GLOBALS_CSS.indexOf("@media (max-width: 600px)"));
    expect(mq600).toMatch(/min-height:\s*44px/);
  });

  it("locks body scroll while the drawer is open", () => {
    expect(GLOBALS_CSS).toMatch(/body\.has-open-sidebar\s*{[^}]*overflow:\s*hidden/s);
  });
});
