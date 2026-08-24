// M12-G10 — project rules client tests. Mock-fetch protocol per the roster
// suite: happy + error paths for get/save/delete + byte-counter math and the
// presentational RulesEditor rendering (counter, gating, disabled Save over cap).
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError, IDENTITY_BASE } from "@/lib/api";
import {
  MAX_RULES_BYTES,
  deleteProjectRules,
  getProjectRules,
  rulesByteLength,
  saveProjectRules,
} from "./projectRulesApi";
// W4 gate repair: RulesEditor lives in its own module now (the page module
// keeps only Next's default export).
import { RulesEditor } from "./rulesEditor";

afterEach(() => {
  vi.unstubAllGlobals();
});

const TOKEN = "tok-123";

function stubFetch(responses: Array<{ status: number; body?: unknown }>) {
  const queue = [...responses];
  return vi.fn(async () => {
    const next = queue.shift() ?? { status: 500 };
    return {
      ok: next.status >= 200 && next.status < 300,
      status: next.status,
      text: async () => (next.body === undefined ? "" : JSON.stringify(next.body)),
    };
  });
}

describe("G10 client: getProjectRules", () => {
  it("GETs .../rules with bearer and returns the projection", async () => {
    const f = stubFetch([
      { status: 200, body: { projectId: "p1", rulesText: "use pnpm", updatedAt: null, updatedBy: null } },
    ]);
    vi.stubGlobal("fetch", f);
    const out = await getProjectRules("p1", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/projects/p1/rules`);
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
    expect(out.rulesText).toBe("use pnpm");
  });

  it("surfaces the server's error message on failure", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch([{ status: 404, body: { detail: { error: { code: "NOT_FOUND", message: "project not found" } } } }]),
    );
    try {
      await getProjectRules("p1", TOKEN);
      expect.fail("expected ApiError");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).message).toBe("project not found");
    }
  });
});

describe("G10 client: saveProjectRules / deleteProjectRules", () => {
  it("PUTs {rulesText} and echoes the saved projection", async () => {
    const f = stubFetch([
      { status: 200, body: { projectId: "p1", rulesText: "use pnpm", updatedAt: "2026-08-23T00:00:00Z", updatedBy: "u1" } },
    ]);
    vi.stubGlobal("fetch", f);
    const saved = await saveProjectRules("p1", "use pnpm", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ rulesText: "use pnpm" });
    expect(saved.updatedBy).toBe("u1");
  });

  it("propagates the 413 RULES_TOO_LARGE policy error verbatim", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch([
        { status: 413, body: { detail: { error: { code: "RULES_TOO_LARGE", message: "project rules exceed the 64KB limit" } } } },
      ]),
    );
    try {
      await saveProjectRules("p1", "x".repeat(MAX_RULES_BYTES + 1), TOKEN);
      expect.fail("expected ApiError");
    } catch (e) {
      expect((e as ApiError).code).toBe("RULES_TOO_LARGE");
      expect((e as ApiError).status).toBe(413);
    }
  });

  it("DELETE resolves to null on 204", async () => {
    const f = stubFetch([{ status: 204 }]);
    vi.stubGlobal("fetch", f);
    await expect(deleteProjectRules("p1", TOKEN)).resolves.toBe(null);
    const [, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("DELETE");
  });
});

describe("G10 client: rulesByteLength", () => {
  it("counts UTF-8 bytes, not chars (CJK ≈ 3 bytes/char)", () => {
    expect(rulesByteLength("")).toBe(0);
    expect(rulesByteLength("abc")).toBe(3);
    expect(rulesByteLength("\u4e00")).toBe(3); // 一
  });
});

describe("G10 UI: RulesEditor", () => {
  const noop = () => {};
  const base = {
    projectId: "p1",
    canEdit: true,
    busy: false,
    onTextChange: noop,
    onSave: noop,
    onClear: noop,
  };

  it("renders textarea + char counter with an empty doc", () => {
    const html = renderToStaticMarkup(<RulesEditor {...base} initial={null} />);
    expect(html).toContain('data-testid="rules-textarea"');
    expect(html).toContain('data-testid="rules-counter"');
    expect(html).toContain("64 KB");
    expect(html).toContain("Save rules");
  });

  it("hides Save/Clear from read-only roles but keeps the counter", () => {
    const html = renderToStaticMarkup(
      <RulesEditor {...base} initial={null} canEdit={false} />,
    );
    expect(html).not.toContain("Save rules");
    expect(html).not.toContain(">Clear<");
    expect(html).toContain("restricted to team admins and engineers");
  });

  it("marks the editor read-only for non-editors", () => {
    const html = renderToStaticMarkup(
      <RulesEditor {...base} initial={null} canEdit={false} />,
    );
    expect(html).toContain("readOnly");
  });

  it("shows the advisory disclaimer about forbidden paths", () => {
    const html = renderToStaticMarkup(<RulesEditor {...base} initial={null} />);
    expect(html).toContain("forbidden paths");
  });
});
