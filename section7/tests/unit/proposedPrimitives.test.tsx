// M16-X4 (raw-17) — unit coverage for the two PROPOSED ui/ additions created
// by this lane: Notice (tinted status/error line + boxed amber banner) and
// Brand (the HARNESS wordmark line). These are NEW files proposed to the DS
// lane (ui/index.ts is frozen, so pages consume them by module path until
// adoption); they get real render coverage here so adoption is low-risk.
//
// Repo protocol: node-env vitest (no jsdom) → react-dom/server renders the
// REAL components and assertions run over emitted markup.
import { describe, expect, it } from "vitest";
// Classic JSX transform (vitest/esbuild) needs React in scope for JSX.
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Notice } from "../../src/components/ui/Notice";
import { Brand } from "../../src/components/ui/Brand";

describe("Notice (proposed primitive)", () => {
  it("default renders a <p> tinted with the semantic tone ink (legacy error-line parity)", () => {
    const html = renderToStaticMarkup(<Notice>boom</Notice>);
    expect(html.startsWith("<p")).toBe(true);
    expect(html).toContain("color:var(--red)");
    expect(html).toContain("boom");
  });

  it("tone map covers all four semantic tones (same vocabulary as Pill)", () => {
    const tones = ["info", "success", "warning", "danger"] as const;
    const inks = ["var(--accent)", "var(--green)", "var(--yellow)", "var(--red)"];
    tones.forEach((tone, i) => {
      const html = renderToStaticMarkup(<Notice tone={tone}>x</Notice>);
      expect(html).toContain(`color:${inks[i]}`);
    });
  });

  it("boxed renders the M12-E2 session-expired banner face verbatim as role=alert", () => {
    const html = renderToStaticMarkup(
      <Notice boxed>Session expired — sign in again.</Notice>,
    );
    expect(html.startsWith("<div")).toBe(true);
    expect(html).toContain('role="alert"');
    expect(html).toContain("background:#3b2f0b"); // amber panel, pixel-parity
    expect(html).toContain("position:fixed");
    expect(html).toContain("Session expired — sign in again.");
  });

  it("role=status is honored for polite notes and testId rides through", () => {
    const html = renderToStaticMarkup(
      <Notice tone="success" role="status" testId="note-x">
        ok
      </Notice>,
    );
    expect(html).toContain('role="status"');
    expect(html).toContain('data-testid="note-x"');
  });
});

describe("Brand (proposed primitive)", () => {
  it("renders the wordmark line with accent ink and wide tracking (.brand parity)", () => {
    const html = renderToStaticMarkup(<Brand>HARNESS</Brand>);
    expect(html.startsWith("<h2")).toBe(true);
    expect(html).toContain("color:var(--accent)");
    expect(html).toContain("letter-spacing:2px");
    expect(html).toContain("HARNESS");
  });
});
