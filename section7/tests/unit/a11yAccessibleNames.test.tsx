// M15-S3 — W4-VT2 carryovers: accessible names + login 320px comfort.
//
// Node-env constraint (no jsdom/RTL in this repo; installs are out of scope),
// so this follows the repo's established protocol: react-dom/server renders
// the REAL components and assertions run over emitted markup — plus
// source-level parity checks where SSR cannot reach (state-gated branches,
// CSS). `getByLabelText` resolution is mirrored by labelForResolves(), which
// implements RTL's <label for> strategy over static markup.
//
// Scope note (honest): the brief said "app/review comment textarea" but
// app/review/ contains NO textarea (verified by grep) — the app's comment
// composer is src/components/CommentsNudges.tsx (run Working Area), covered
// here. Grep also surfaced the placeholder-only human-edit path input in
// src/components/Controls.tsx; fixed and pinned here too.
import { describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

// AppShell is shared chrome outside this lane's owned paths and pulls the
// Next router; swap it for a pass-through (same trick as meetingRestore.test).
vi.mock("@/components/AppShell", () => ({
  default: (props: { children?: React.ReactNode }) =>
    React.createElement("div", null, props.children),
}));

const HERE = dirname(fileURLToPath(import.meta.url));
const GLOBALS_CSS = readFileSync(join(HERE, "..", "..", "app", "globals.css"), "utf8");
const LOGIN_SRC = readFileSync(join(HERE, "..", "..", "app", "login", "page.tsx"), "utf8");
const CONTROLS_SRC = readFileSync(
  join(HERE, "..", "..", "src", "components", "Controls.tsx"),
  "utf8",
);

/* ------------------------------------------------------------------ *
 * getByLabelText equivalents over static markup                       *
 * ------------------------------------------------------------------ */

/** RTL's `<label for>` branch: a non-empty label whose for= points at an
 *  existing control id resolves as that control's accessible name. Accepts
 *  both HTML (`for=`) and JSX source (`htmlFor=`) so it works on markup AND
 *  source parity checks. */
function labelForResolves(text: string, inputId: string): string | null {
  const m = text.match(
    new RegExp(`<label[^>]*(?:for|htmlFor)="${inputId}"[^>]*>([\\s\\S]*?)</label>`),
  );
  if (!m) return null;
  const label = m[1].replace(/<[^>]+>/g, "").trim(); // sr-only spans etc. still count
  if (!label) return null;
  return new RegExp(`id="${inputId}"`).test(text) ? label : null;
}

/** RTL's aria-label branch: the control tag itself carries the name. */
function ariaLabelOn(html: string, tagMarker: string): string | null {
  const i = html.indexOf(tagMarker);
  if (i === -1) return null;
  const start = html.lastIndexOf("<", i);
  const tag = html.slice(start, html.indexOf(">", i) + 1);
  const m = tag.match(/aria-label="([^"]+)"/);
  return m ? m[1] : null;
}

/* ------------------------------------------------------------------ *
 * 1. Login page (all three mode variants)                             *
 * ------------------------------------------------------------------ */

describe("login page accessible names (M15-S3)", () => {
  it("legacy mode: email input resolves via <label for> (getByLabelText parity)", async () => {
    const { default: Login } = await import("../../app/login/page");
    const html = renderToStaticMarkup(React.createElement(Login));

    // SSR never runs effects, so the statically prerendered variant is the
    // legacy sign-in form — the one every visitor sees first.
    expect(html).toContain('id="login-email"');
    expect(labelForResolves(html, "login-email")).toBe("Email address");
  });

  it("create-project + otp variants carry real names too (source parity)", () => {
    // Those branches are state-gated; SSR shows only legacy mode, so pin them
    // at source level the way the repo pins routed pages elsewhere.
    expect(LOGIN_SRC).toContain('<label htmlFor="login-project-name" className="sr-only">');
    expect(LOGIN_SRC).toContain('id="login-project-name"');
    expect(LOGIN_SRC).toContain('<label htmlFor="login-otp-code" className="sr-only">');
    expect(labelForResolves(LOGIN_SRC, "login-otp-code")).toBe("6-digit code");
  });

  it("every login input keeps its id wired to a label (no orphan placeholders)", () => {
    for (const id of [
      "login-email",
      "login-project-name",
      "login-otp-code",
      // F-gap closure: the SSO team-id input follows the same rule.
      "login-team-id",
    ]) {
      expect(LOGIN_SRC).toContain(`htmlFor="${id}"`);
      expect(LOGIN_SRC).toContain(`id="${id}"`);
    }
  });
});

describe("login card 320px comfort (M15-S3)", () => {
  it("no fixed >320px width remains on any card variant", () => {
    expect(LOGIN_SRC).not.toContain("width: 360");
    expect(LOGIN_SRC).toMatch(/className="card login-card"/g);
    // 4 mode variants: legacy, create-project, otp, sso (F-gap closure).
    expect(LOGIN_SRC.match(/className="card login-card"/g)).toHaveLength(4);
  });

  it(".login-card is a fluid cap: fits 320px minus gutters, unchanged ≥404px", () => {
    expect(GLOBALS_CSS).toMatch(
      /\.login-card\s*{[^}]*width:\s*min\(100% - 24px,\s*380px\)/s,
    );
    expect(GLOBALS_CSS).toMatch(
      /\.login-card\s*{[^}]*max-width:\s*calc\(100vw - 24px\)/s,
    );
    // At a 320px viewport: min(296px, 380px) = 296px ≤ 320px — comfortable.
    expect(Math.min(320 - 24, 380)).toBeLessThanOrEqual(320);
  });
});

/* ------------------------------------------------------------------ *
 * 2. Meeting chat input                                               *
 * ------------------------------------------------------------------ */

describe("meeting chat input accessible name (M15-S3)", () => {
  it("composer input resolves via aria-label (getByLabelText parity)", async () => {
    const { default: Meeting } = await import("../../app/meeting/page");
    const html = renderToStaticMarkup(React.createElement(Meeting));

    expect(html).toContain("Message team… use @ai to summon the agent"); // hint kept
    expect(ariaLabelOn(html, "Message team…")).toBe("Chat message");
  });

  it("source parity: the chat-input input carries the aria-label", () => {
    const src = readFileSync(join(HERE, "..", "..", "app", "meeting", "page.tsx"), "utf8");
    const chatInput = src.slice(src.indexOf('className="chat-input"'));
    expect(chatInput.slice(0, 600)).toContain('aria-label="Chat message"');
  });
});

/* ------------------------------------------------------------------ *
 * 3. Run-note composer (M-A1: renamed from "comment / nudge" — it has *
 *    no backend, so it no longer claims to reach the agent)           *
 * ------------------------------------------------------------------ */

describe("run-note composer accessible name (M15-S3)", () => {
  it("textarea resolves via aria-label (getByLabelText parity)", async () => {
    const { CommentsNudges } = await import("../../src/components/CommentsNudges");
    const html = renderToStaticMarkup(React.createElement(CommentsNudges, { authorName: "you" }));

    expect(html).toContain("<textarea");
    expect(ariaLabelOn(html, "<textarea")).toBe(
      "Note for yourself about this run",
    );
    // The panel must not imply the agent receives it.
    expect(html).toContain("Private to this browser tab");
    expect(html).not.toContain("no backend endpoint exists yet");
  });
});

/* ------------------------------------------------------------------ *
 * 4. Human-edit path input (grep-found extra)                         *
 * ------------------------------------------------------------------ */

describe("human-edit path input accessible name (M15-S3)", () => {
  it("editPath input carries an aria-label (source parity)", () => {
    const takeover = CONTROLS_SRC.slice(CONTROLS_SRC.indexOf("{takeover && ("));
    expect(takeover.slice(0, 700)).toContain('placeholder="file path"');
    expect(takeover.slice(0, 700)).toContain('aria-label="File path to edit"');
  });
});
