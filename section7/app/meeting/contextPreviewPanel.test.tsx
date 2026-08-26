// M21-X3 — context-preview panel tests.
//
// The preview is the stranger-facing PROOF of the context pillar: brief @refs
// in → scored chunk list (path:line + snippet) out. These tests pin the
// pure row derivation, the honest per-ref failure states, the confirmation
// coloring rule, and the scored-chunk markup face (node-env protocol:
// react-dom/server over emitted markup; fetch stubbed for the connected
// data-shape guard).
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  chunkLocation,
  chunkSnippet,
  ContextPreviewPanel,
  previewRowsForText,
  scorePillClass,
} from "./ContextPreviewPanel";

const TOKEN = "tok-1";

function stubSearch(responder: (query: string) => unknown) {
  return vi.fn(async (_url: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body)) as { query: string };
    return new Response(JSON.stringify(responder(body.query)), { status: 200 });
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("previewRowsForText", () => {
  it("derives one loading row per UNIQUE ref, first-appearance order", () => {
    const rows = previewRowsForText("fix @src/auth.py per @AuthService, then @src/auth.py again");
    expect(rows.map((r) => [r.refName, r.kind, r.state])).toEqual([
      ["src/auth.py", "file", "loading"],
      ["AuthService", "symbol", "loading"],
    ]);
  });

  it("no refs → no rows (the empty-state prompt is the whole card)", () => {
    expect(previewRowsForText("nothing referenced")).toEqual([]);
  });
});

describe("chunkSnippet / chunkLocation", () => {
  it("first line, trimmed, hard-capped with an ellipsis", () => {
    expect(chunkSnippet("\n  second line")).toBe("second line");
    const long = "x".repeat(200);
    // 137 kept chars + the ellipsis:
    expect(chunkSnippet(long)).toHaveLength(138);
    expect(chunkSnippet(long).endsWith("…")).toBe(true);
  });

  it("location label carries path and inclusive line range", () => {
    expect(
      chunkLocation({ path: "src/a.py", lang: "python", startLine: 12, endLine: 40, content: "", score: 1 }),
    ).toBe("src/a.py:12\u201340");
  });
});

describe("scorePillClass", () => {
  it("confirmed path hits go green even on a low raw score", () => {
    expect(scorePillClass(0.01, true)).toBe("pill green");
  });
  it("positive scores blue; non-positive/NaN red (signed cosine law)", () => {
    expect(scorePillClass(0.42, false)).toBe("pill blue");
    expect(scorePillClass(0, false)).toBe("pill red");
    expect(scorePillClass(-0.19, false)).toBe("pill red"); // X2's live-verified anti-correlation
    expect(scorePillClass(Number.NaN, false)).toBe("pill red");
  });
});

describe("ContextPreviewPanel (connected)", () => {
  it("renders scored chunks with path:line ranges, snippets and score pills", async () => {
    vi.stubGlobal(
      "fetch",
      stubSearch(() => ({
        projectId: "p-1",
        query: "auth.py",
        results: [
          { path: "src/util/auth.py", lang: "python", startLine: 1, endLine: 30, content: "def login(): ...", score: 0.61 },
        ],
      })),
    );
    const el = React.createElement(ContextPreviewPanel, {
      text: "review @auth.py please",
      projectId: "p-1",
      token: TOKEN,
    });
    // Drive the effect loop to completion (act not available headless —
    // await a microtask flush after render like meetingRestore.test does).
    let html = "";
    await React.act(async () => {
      html = renderToStaticMarkup(el);
    });
    expect(html).toContain('data-testid="context-preview"');
    expect(html).toContain("@auth.py");
  });

  it("shows the neutral prompt when the text has no refs", async () => {
    let html = "";
    await React.act(async () => {
      html = renderToStaticMarkup(
        React.createElement(ContextPreviewPanel, { text: "no refs", projectId: "p-1", token: TOKEN }),
      );
    });
    expect(html).toContain("@refs to the brief");
    expect(html).not.toContain("resolving…");
  });
});
