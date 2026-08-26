// M21-X3 — tree-browser searchable index tests.
//
// The file-tree browser's new search box runs against Section 11's frozen
// W21-C1 API (same as the meeting @ref autocomplete). Node-env protocol:
// pure helpers direct, searchIndex with stubbed global fetch, presentational
// IndexSearchBar via react-dom/server.
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError } from "@/lib/api";
import {
  guardIndexHits,
  indexSearchMode,
  pathsFromHits,
  searchIndex,
} from "./treeApi";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("indexSearchMode", () => {
  it("file-suffix queries browse files; everything else hunts symbols", () => {
    expect(indexSearchMode("auth.py")).toBe("files");
    expect(indexSearchMode("src/util/auth.tsx")).toBe("files");
    expect(indexSearchMode("  AuthService ")).toBe("symbols");
    expect(indexSearchMode("run_pipeline")).toBe("symbols");
    expect(indexSearchMode("")).toBe("symbols"); // mode is total; caller gates the call
  });
});

describe("pathsFromHits / guardIndexHits", () => {
  it("distinct paths in hit order, capped", () => {
    const hits = [
      { path: "a.py", startLine: 1, endLine: 2, score: 1 },
      { path: "b.py", startLine: 5, endLine: 9, score: 0.9 },
      { path: "a.py", startLine: 30, endLine: 44, score: 0.8 },
    ];
    expect(pathsFromHits(hits)).toEqual(["a.py", "b.py"]);
    expect(pathsFromHits(hits, 1)).toEqual(["a.py"]);
  });

  it("drops drifted rows (missing/non-finite fields) instead of crashing", () => {
    const hits = guardIndexHits([
      { path: "ok.py", startLine: 1, endLine: 2, score: 0.5 },
      { path: "bad.py", startLine: "1", endLine: 2, score: 0.5 }, // string line
      null,
      42,
    ]);
    expect(hits.map((h) => h.path)).toEqual(["ok.py"]);
  });
});

describe("searchIndex over the wire", () => {
  function stubFetch(body: unknown, status = 200) {
    return vi.fn(async () => new Response(JSON.stringify(body), { status }));
  }

  it("POSTs the camelCase W21-C1 body to Section 11 with bearer auth", async () => {
    const f = stubFetch({ projectId: "p-9", query: "auth.py", results: [] });
    vi.stubGlobal("fetch", f);
    await searchIndex("p-9", "auth.py", "tok-1", { topK: 12 });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(String(url)).toContain("/api/v1/context/search");
    const headers = init.headers as Record<string, string>;
    expect(headers.authorization).toBe("Bearer tok-1");
    expect(JSON.parse(String(init.body))).toEqual({
      query: "auth.py",
      projectId: "p-9",
      topK: 12,
    });
  });

  it("file mode collapses to ONE row per path carrying its best score", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        projectId: "p-9",
        query: "auth.py",
        results: [
          { path: "src/auth.py", lang: "py", startLine: 1, endLine: 30, content: "x", score: 0.4 },
          { path: "src/auth.py", lang: "py", startLine: 40, endLine: 60, content: "y", score: 0.9 },
          { path: "old/auth.py", lang: "py", startLine: 1, endLine: 10, content: "z", score: 0.2 },
        ],
      }),
    );
    const res = await searchIndex("p-9", "auth.py", "tok-1");
    expect(res.mode).toBe("files");
    expect(res.hits).toEqual([
      // best score per path wins; distinct-path order preserved
      { path: "src/auth.py", startLine: 1, endLine: 1, score: 0.9 },
      { path: "old/auth.py", startLine: 1, endLine: 1, score: 0.2 },
    ]);
  });

  it("symbol mode keeps scored chunk rows with their real line ranges", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        projectId: "p-9",
        query: "AuthService",
        results: [
          { path: "src/auth/service.py", lang: "py", startLine: 88, endLine: 120, content: "def login", score: 0.77 },
        ],
      }),
    );
    const res = await searchIndex("p-9", "AuthService", "tok-1");
    expect(res.mode).toBe("symbols");
    expect(res.hits[0]).toEqual({
      path: "src/auth/service.py",
      startLine: 88,
      endLine: 120,
      score: 0.77,
    });
  });

  it("grammar parity pin: dotted @Class.method is a FILE ref in S4's parser — mirror must not diverge", async () => {
    // context_resolver.parse_refs checks _FILE_SUFFIX BEFORE the symbol shape,
    // so ".login" counts as a file suffix and "@AuthService.login" is a file
    // ref there. Verified against the implementation; if S4 reorders those
    // branches, change BOTH sides.
    expect(indexSearchMode("AuthService.login")).toBe("files");
  });

  it("empty query short-circuits honestly without a network call", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const res = await searchIndex("p-9", "   ", "tok-1");
    expect(f).not.toHaveBeenCalled();
    expect(res.hits).toEqual([]);
  });

  it("maps error envelopes onto ApiError with the contract code", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({ detail: { error: { code: "ROLE_DENIED", message: "nope" } } }, 403),
    );
    const err = await searchIndex("p-9", "x", "tok").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("ROLE_DENIED");
  });
});
