// M14-W4 DS lane — primitive render + a11y spot checks.
// SSR-string protocol (matches the repo's node-environment vitest style):
// structural/aria assertions over rendered markup, no DOM event simulation
// except where noted (Modal focus behaviors need a real document, so they are
// guarded and skipped gracefully in a node environment).
import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { Button, Card, Modal, Pill, StatusPill, Table, cellStyle } from "@/components/ui";

const markup = (el: React.ReactElement) => renderToStaticMarkup(el);

describe("Pill", () => {
  it("renders a static span with tone colors from tokens", () => {
    const html = markup(<Pill tone="green">done</Pill>);
    expect(html).toContain("<span");
    expect(html).toContain("var(--green)");
    expect(html).toContain("done");
  });

  it("maps all four tones to their globals.css vars", () => {
    expect(markup(<Pill tone="red">x</Pill>)).toContain("var(--red)");
    expect(markup(<Pill tone="yellow">x</Pill>)).toContain("var(--yellow)");
    expect(markup(<Pill tone="blue">x</Pill>)).toContain("var(--accent)");
  });

  it("StatusPill resolves known statuses and degrades unknowns to blue", () => {
    expect(markup(<StatusPill status="done">done</StatusPill>)).toContain("var(--green)");
    expect(markup(<StatusPill status="blocked">blocked</StatusPill>)).toContain("var(--red)");
    expect(markup(<StatusPill status="admin">admin</StatusPill>)).toContain("var(--green)");
    // honest fallthrough for unknown server-side states
    expect(markup(<StatusPill status="mystery_state">?</StatusPill>)).toContain("var(--accent)");
  });

  it("interactive pills render as keyboard-operable buttons with aria-pressed", () => {
    const html = markup(
      <Pill tone="blue" onClick={() => {}} active>
        30d
      </Pill>,
    );
    expect(html).toContain("<button");
    expect(html).toContain('type="button"');
    expect(html).toContain('aria-pressed="true"');
  });
});

describe("Button", () => {
  it("defaults to type=button secondary variant on the .btn box model", () => {
    const html = markup(<Button>Go</Button>);
    expect(html).toContain('type="button"');
    expect(html).toContain("var(--panel-2)");
    expect(html).toContain("7px 14px"); // .btn padding
    expect(html).toContain("Go");
  });

  it("primary/danger variants use soft-filled accent faces", () => {
    expect(markup(<Button variant="primary">Save</Button>)).toContain("var(--accent)");
    expect(markup(<Button variant="danger">Delete</Button>)).toContain("var(--red)");
  });

  it("disabled renders natively disabled", () => {
    expect(markup(<Button disabled>Can't</Button>)).toContain("disabled");
  });

  it("loading swaps label text, sets aria-busy and disables", () => {
    const html = markup(
      <Button loading loadingText="Removing…">
        Yes, remove
      </Button>,
    );
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain("Removing…");
    expect(html).not.toContain("Yes, remove");
    expect(html).toContain("disabled");
  });

  it("submit type passes through for form callers", () => {
    expect(markup(<Button type="submit">Create invite</Button>)).toContain('type="submit"');
  });
});

describe("Card", () => {
  it("renders section.card with the uppercase micro-label heading", () => {
    const html = markup(
      <Card title="Total cost">
        <div>$1.00</div>
      </Card>,
    );
    expect(html).toContain("<section");
    expect(html).toContain("<h2");
    expect(html).toContain("uppercase");
    expect(html).toContain("$1.00");
  });

  it("supports div rendering and aria-label for titleless cards", () => {
    const html = markup(
      <Card as="div" aria-label="KPI tile">
        <span>x</span>
      </Card>,
    );
    expect(html).not.toContain("<h2");
    expect(html).toContain('aria-label="KPI tile"');
    expect(html.startsWith("<div")).toBe(true);
  });
});

describe("Table", () => {
  it("keeps native table semantics inside an overflow-x scroll wrapper", () => {
    const html = markup(
      <Table caption="Runs">
        <thead>
          <tr>
            <th>A</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>b</td>
          </tr>
        </tbody>
      </Table>,
    );
    expect(html).toContain('overflow-x:auto');
    expect(html).toContain("<table");
    expect(html).toContain("<thead>");
    expect(html).toContain("<tbody>");
    expect(html).toContain("<td>b</td>");
  });

  it("caption is present but visually hidden", () => {
    const html = markup(
      <Table caption="Secret caption">
        <tbody />
      </Table>,
    );
    expect(html).toContain("<caption");
    expect(html).toContain("clip:rect(0, 0, 0, 0)");
    expect(html).toContain("Secret caption");
  });

  it("exports the shared legacy cell treatment", () => {
    expect(cellStyle.padding).toBe("6px 8px");
    expect(cellStyle.borderBottom).toBe("1px solid var(--border)");
  });
});

describe("Modal", () => {
  it("closed modal renders nothing", () => {
    expect(
      markup(
        <Modal open={false} onClose={() => {}} title="Hidden">
          body
        </Modal>,
      ),
    ).toBe("");
  });

  it("open modal carries role=dialog, aria-modal, labelledby → title", () => {
    const html = markup(
      <Modal open onClose={() => {}} title="Confirm removal">
        <button type="button">Yes</button>
      </Modal>,
    );
    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    const labelledby = html.match(/aria-labelledby="([^"]+)"/)?.[1];
    expect(labelledby).toBeTruthy();
    expect(html).toContain(`id="${labelledby}"`);
    expect(html).toContain("Confirm removal");
  });

  it("panel is focusable (tabIndex=-1) so initial focus has a fallback target", () => {
    const html = markup(
      <Modal open onClose={() => {}} title="T">
        <p>no controls here</p>
      </Modal>,
    );
    expect(html).toContain('tabindex="-1"');
  });

  it("resolveTabTarget: full wrap-around decision table", async () => {
    const { resolveTabTarget } = await import("@/components/ui/Modal");
    // forward cycle with 3 focusables
    expect(resolveTabTarget(0, 3, false)).toBe(1);
    expect(resolveTabTarget(1, 3, false)).toBe(2);
    expect(resolveTabTarget(2, 3, false)).toBe(0); // last wraps to first
    // reverse cycle
    expect(resolveTabTarget(2, 3, true)).toBe(1);
    expect(resolveTabTarget(1, 3, true)).toBe(0);
    expect(resolveTabTarget(0, 3, true)).toBe(2); // first wraps to last
    // focus outside the panel is pulled back in
    expect(resolveTabTarget(-1, 3, false)).toBe(0);
    expect(resolveTabTarget(-1, 3, true)).toBe(2);
    // no focusables: nothing to move to
    expect(resolveTabTarget(0, 0, false)).toBe(-1);
  });

  // VT5 verifier addition (M14-W4): the Esc-close branch cannot be event-driven
  // in this node-environment suite (no jsdom; no new deps allowed), so the
  // keyboard contract is pinned at source level — same protocol AppShell
  // .responsive uses against globals.css. A refactor dropping the overlay's
  // onKeyDown wiring or the Escape branch now fails HERE instead of silently.
  it("Esc-close contract wired on the overlay (source-pinned)", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync(
      new URL("../../src/components/ui/Modal.tsx", import.meta.url),
      "utf8",
    );
    expect(src).toContain('onKeyDown={handleKeyDown}'); // overlay wires the handler
    expect(src).toContain('e.key === "Escape"'); // Esc recognized...
    expect(src).toMatch(/Escape"[\s\S]{0,120}onClose\(\)/); // ...and closes
  });
});

describe("tokens.ts integrity", () => {
  it("token palette matches globals.css :root exactly", async () => {
    const { colors } = await import("@/components/tokens");
    expect(colors.bg).toBe("#1a1814");
    expect(colors.panel).toBe("#1e1c18");
    expect(colors.panel2).toBe("#252220");
    expect(colors.border).toBe("#3d3930");
    expect(colors.text).toBe("#faf9f6");
    expect(colors.muted).toBe("#a8a296");
    expect(colors.accent).toBe("#8ee5a1");
    expect(colors.green).toBe("#5cb870");
    expect(colors.red).toBe("#ff6b6b");
    expect(colors.yellow).toBe("#e5a814");
  });
});
