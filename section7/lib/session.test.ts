// W1-C2 — session bootstrap tests (C-006). Node-env safe: the storage helpers
// no-op without a window, exactly like the @/lib/auth guards the existing
// auth.test.ts pins. Wire calls are stubbed at fetch level (no mocks of our
// own module — the real listProjects/createAndActivateProject run).
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  bootstrapProjectSession,
  bootstrapSucceeded,
  createAndActivateProject,
} from "./session";
import { IDENTITY_BASE } from "@/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubOk(responseBody: unknown, status = 200) {
  return vi.fn(async () => ({
    ok: true,
    status,
    text: async () => JSON.stringify(responseBody),
  }));
}

const TOKEN = "tok-1";

describe("bootstrapProjectSession — real identity or honest routing", () => {
  it("adopts the first REAL project of the caller's team", async () => {
    const f = stubOk({
      items: [
        { id: "proj-real-9", name: "Atlas", repoFullName: null },
        { id: "proj-real-10", name: "B", repoFullName: "o/b" },
      ],
      nextCursor: null,
    });
    vi.stubGlobal("fetch", f);
    const { outcome, project } = await bootstrapProjectSession(TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/projects?limit=100`);
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
    expect(outcome).toBe("project-selected");
    expect(project?.id).toBe("proj-real-9");
    expect(bootstrapSucceeded(outcome)).toBe(true);
  });

  it("reports needs-project when the team has zero rows — never invents an id", async () => {
    vi.stubGlobal("fetch", stubOk({ items: [], nextCursor: null }));
    const { outcome, project } = await bootstrapProjectSession(TOKEN);
    expect(outcome).toBe("needs-project");
    expect(project).toBeNull();
    expect(bootstrapSucceeded(outcome)).toBe(false);
  });

  it("reports unresolved when the identity service is down — nothing fabricated", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("ECONNREFUSED");
      }),
    );
    const { outcome, project } = await bootstrapProjectSession(TOKEN);
    expect(outcome).toBe("unresolved");
    expect(project).toBeNull();
    expect(bootstrapSucceeded(outcome)).toBe(false);
  });
});

describe("createAndActivateProject — POST /api/v1/projects (frozen S1 route)", () => {
  it("POSTs {name} with bearer and mirrors the 201 body field-for-field", async () => {
    const f = stubOk({ id: "proj-new", name: "First", repoFullName: null }, 201);
    vi.stubGlobal("fetch", f);
    const project = await createAndActivateProject("  First  ", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/projects`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ name: "First" }); // trimmed
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
    expect(project.id).toBe("proj-new");
    expect(project.name).toBe("First");
    expect(project.repoFullName).toBeNull(); // nullability mirrored
  });

  it("rejects blank names client-side without a network call", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    await expect(createAndActivateProject("   ", TOKEN)).rejects.toThrow(
      /name is required/i,
    );
    expect(f).not.toHaveBeenCalled();
  });

  it("surfaces the frozen error envelope as ApiError(code)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 422,
        text: async () =>
          JSON.stringify({ error: { code: "NAME_REQUIRED", message: "project name required" } }),
      })),
    );
    // A non-blank name so the request actually reaches the wire.
    const err = await createAndActivateProject("Valid Name", TOKEN).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("NAME_REQUIRED");
  });
});
