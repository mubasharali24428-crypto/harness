// M21-X3 — contextApi tests (node env; fetch stubbed, pure helpers direct).
// Contract under test: Section 11's FROZEN W21-C1 search wire shape
// {query, projectId, topK} → {projectId, query,
// results:[{path, lang, startLine, endLine, content, score}]}, plus the
// client mirror of S4's @ref grammar (parse/retrieval-query/confirmation).
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  activeRefToken,
  applyRefSelection,
  clampTopK,
  CONTEXT_BASE,
  makeRetrievalQuery,
  parseRefs,
  pathConfirms,
  searchContext,
  uniqueRefNames,
} from "@/lib/contextApi";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("searchContext (W21-C1 wire contract)", () => {
  it("POSTs the camelCase body to the frozen route with bearer auth", async () => {
    const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      expect(String(url)).toBe(`${CONTEXT_BASE}/api/v1/context/search`);
      const headers = init?.headers as Record<string, string>;
      expect(headers.authorization).toBe("Bearer tok-1");
      expect(JSON.parse(String(init?.body))).toEqual({
        query: "auth",
        projectId: "p-1",
        topK: 4,
      });
      return new Response(
        JSON.stringify({
          projectId: "p-1",
          query: "auth",
          results: [
            {
              path: "src/auth.py",
              lang: "python",
              startLine: 1,
              endLine: 40,
              content: "def login(): ...",
              score: 0.42,
            },
          ],
        }),
        { status: 200 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const res = await searchContext({ query: "auth", projectId: "p-1", topK: 4 }, "tok-1");
    expect(res.results).toHaveLength(1);
    // The frozen six-field shape survives the guard untouched.
    expect(res.results[0]).toEqual({
      path: "src/auth.py",
      lang: "python",
      startLine: 1,
      endLine: 40,
      content: "def login(): ...",
      score: 0.42,
    });
  });

  it("drops drifted rows instead of crashing on results.map", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            projectId: "p-1",
            query: "q",
            results: [
              { path: "ok.py", lang: "python", startLine: 1, endLine: 2, content: "x", score: 1 },
              { junk: true },
              null,
              { path: "half.py" },
            ],
          }),
          { status: 200 },
        ),
      ),
    );
    const res = await searchContext({ query: "q", projectId: "p-1" }, "tok-1");
    expect(res.results).toHaveLength(1);
    expect(res.results[0]!.path).toBe("ok.py");
  });

  it("short-circuits empty queries to an honest zero-hit result (no call)", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const res = await searchContext({ query: "   ", projectId: "p-1" }, "tok-1");
    expect(f).not.toHaveBeenCalled();
    expect(res.results).toEqual([]);
  });

  it("maps error envelopes onto ApiError with the contract code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            detail: { error: { code: "ROLE_DENIED", message: "nope" } },
          }),
          { status: 403 },
        ),
      ),
    );
    const err = await searchContext({ query: "x", projectId: "p" }, "tok").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("ROLE_DENIED");
  });

  it("clamps topK into the server window [1, 50]", async () => {
    expect(clampTopK(undefined)).toBe(8);
    expect(clampTopK(0)).toBe(1);
    expect(clampTopK(-5)).toBe(1);
    expect(clampTopK(999)).toBe(50);
    expect(clampTopK(Number.NaN)).toBe(8);
    expect(clampTopK(7.9)).toBe(7);
  });
});

describe("parseRefs (client mirror of S4's grammar)", () => {
  it("classifies file refs by suffix and symbol refs by identifier shape", () => {
    const refs = parseRefs("touch @src/util/auth.py and @AuthService, then @run_pipeline.");
    expect(refs.map((r) => [r.kind, r.name])).toEqual([
      ["file", "src/util/auth.py"],
      ["symbol", "AuthService"],
      ["symbol", "run_pipeline"],
    ]);
  });

  it("keeps sentence punctuation out of matches (trailing-dot rule)", () => {
    const refs = parseRefs("see @auth.py.");
    expect(refs).toHaveLength(1);
    expect(refs[0]!.name).toBe("auth.py");
  });

  it("records spans that include the @", () => {
    const refs = parseRefs("go @pkg.mod now");
    expect(refs[0]!.span).toEqual([3, 11]);
  });

  it("matches S4's grammar exactly: a bare word after @ parses as a symbol ref", () => {
    // Parity pin — verified against context_resolver.parse_refs: _SYMBOL
    // full-matches "ai", so "@ai help" IS one symbol ref there. The UI must
    // not diverge; if S4 ever whitelists command words, change BOTH.
    const refs = parseRefs("@ai help");
    expect(refs.map((r) => r.name)).toEqual(["ai"]);
    expect(parseRefs("@")).toEqual([]);
    expect(parseRefs("no refs here")).toEqual([]);
  });
});

describe("uniqueRefNames / makeRetrievalQuery / pathConfirms", () => {
  it("dedupes by name keeping first appearance", () => {
    const refs = parseRefs("@a.py @b.py @a.py");
    expect(uniqueRefNames(refs)).toEqual(["a.py", "b.py"]);
  });

  it("file refs query by basename (S4's make_retrieval_query)", () => {
    expect(makeRetrievalQuery("src/util/auth.py")).toBe("auth.py");
    expect(makeRetrievalQuery("auth.py")).toBe("auth.py");
    expect(makeRetrievalQuery("AuthService")).toBe("AuthService");
  });

  it("confirms chunk paths by exact match or whole-segment suffix", () => {
    expect(pathConfirms("src/util/auth.py", "util/auth.py")).toBe(true);
    expect(pathConfirms("src/util/auth.py", "auth.py")).toBe(true);
    expect(pathConfirms("src/util/auth.py", "til/auth.py")).toBe(false);
    expect(pathConfirms("src/util/auth.py", "other.py")).toBe(false);
    expect(pathConfirms("", "auth.py")).toBe(false);
  });
});

describe("activeRefToken / applyRefSelection (composer rewrite)", () => {
  it("detects an @ token still being typed at the end of the draft", () => {
    expect(activeRefToken("hello @")).toBe("");
    expect(activeRefToken("@src/ma")).toBe("src/ma");
    expect(activeRefToken("(@pkg")).toBe("pkg");
    expect(activeRefToken("done @auth.py ")).toBe(null);
    expect(activeRefToken("email me a@b.com")).toBe(null); // not preceded by space/start
  });

  it("rewrites the trailing partial into the chosen ref plus one space", () => {
    expect(applyRefSelection("hello @src/ma", "src/main.ts")).toBe("hello @src/main.ts ");
    expect(applyRefSelection("@", "README.md")).toBe("@README.md ");
    expect(applyRefSelection("plain text", "x.py")).toBe("plain text"); // no-op safety
  });
});
