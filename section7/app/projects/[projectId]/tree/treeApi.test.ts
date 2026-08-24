// M12-G8 — tree API client tests (node env, pure functions + fetch stubbing).
// Same protocol as apiClients.test.ts: stub global fetch, assert request shape
// and the shape-guarded result; error mapping goes through parseApiError so
// the S5 contract envelope {"detail":{"error":{...}}} lands as ApiError.code.
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  buildTreeQueryString,
  formatFileSize,
  listTree,
  normalizeTreePath,
  parentTreePath,
  treeBreadcrumbs,
} from "./treeApi";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("normalizeTreePath", () => {
  it("strips leading/trailing slashes and collapses doubles", () => {
    expect(normalizeTreePath("/src//lib/")).toBe("src/lib");
    expect(normalizeTreePath("  src ")).toBe("src");
    expect(normalizeTreePath(null)).toBe("");
    expect(normalizeTreePath("")).toBe("");
  });
});

describe("buildTreeQueryString", () => {
  it("omits the query at the root, encodes deeper paths", () => {
    expect(buildTreeQueryString("")).toBe("");
    expect(buildTreeQueryString("src/lib")).toBe("?path=src%2Flib");
  });
});

describe("listTree", () => {
  it("GETs the sandbox tree route with bearer auth and shape-guards entries", async () => {
    const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      expect(String(url)).toBe(
        "http://localhost:8005/api/v1/sandbox/proj-9/tree?path=src",
      );
      expect((init?.headers as Record<string, string>).authorization).toBe("Bearer tok-1");
      return new Response(
        JSON.stringify({
          path: "src",
          entries: [
            { name: "main.py", type: "file", size: 12 },
            { name: "util", type: "dir", size: 0 },
            { junk: true }, // drifted entry — filtered out, not a crash
          ],
        }),
        { status: 200 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const listing = await listTree("proj-9", "src", "tok-1");
    expect(listing.path).toBe("src");
    expect(listing.entries).toEqual([
      { name: "main.py", type: "file", size: 12 },
      { name: "util", type: "dir", size: 0 },
    ]);
  });

  it("maps the S5 contract 404 envelope onto ApiError with its code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            detail: { error: { code: "PATH_NOT_FOUND", message: "no such directory" } },
          }),
          { status: 404 },
        ),
      ),
    );
    const err = await listTree("proj-x", "../etc", "tok-1").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).code).toBe("PATH_NOT_FOUND");
  });

  it("treats a drifted non-array body as an honest empty folder", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({}), { status: 200 })),
    );
    const listing = await listTree("proj-9", "", "tok-1");
    expect(listing.entries).toEqual([]);
  });
});

describe("parentTreePath / treeBreadcrumbs", () => {
  it("walks up one level and bottoms out at the root", () => {
    expect(parentTreePath("a/b")).toBe("a");
    expect(parentTreePath("a")).toBe("");
    expect(parentTreePath("")).toBe("");
  });

  it("builds cumulative breadcrumbs rooted at the project name", () => {
    expect(treeBreadcrumbs("src/lib", "proj-1")).toEqual([
      { name: "proj-1", path: "" },
      { name: "src", path: "src" },
      { name: "lib", path: "src/lib" },
    ]);
    expect(treeBreadcrumbs("", "proj-1")).toEqual([{ name: "proj-1", path: "" }]);
  });
});

describe("formatFileSize", () => {
  it("renders honest human sizes", () => {
    expect(formatFileSize(0)).toBe("0 B");
    expect(formatFileSize(512)).toBe("512 B");
    expect(formatFileSize(1536)).toBe("1.5 KB");
    expect(formatFileSize(1024 * 1024)).toBe("1.0 MB");
    expect(formatFileSize(-1)).toBe("—");
    expect(formatFileSize(Number.NaN)).toBe("—");
  });
});
