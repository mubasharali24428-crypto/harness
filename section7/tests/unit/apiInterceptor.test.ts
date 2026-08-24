import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Principal } from "@contracts/roles";
import { ApiError, getRunStatus } from "@/lib/api";
import {
  getGoal,
  getPrId,
  getProjectId,
  getRunId,
  getToken,
  getUserId,
  loadSession,
  saveSession,
  setGoal,
  setPrId,
  setProjectId,
  setRunId,
  setUserId,
} from "@/lib/auth";

// M12-E2 H-003: apiFetch's 401 branch must clear the ENTIRE client-side
// session (token + every loose harness.* key) and bounce to /login?expired=1.
// S7's vitest runs in the node env, so these tests install a minimal fake
// window (localStorage + location) — exactly the two surfaces the interceptor
// touches — and restore the bare-node world afterwards.

const principal: Principal = { userId: "u-1", teamId: "t-1", role: "admin" };

function installFakeBrowser(): void {
  const store = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (k: string) => (store.has(k) ? (store.get(k) as string) : null),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    removeItem: (k: string) => {
      store.delete(k);
    },
    setItem: (k: string, v: string) => {
      store.set(k, v);
    },
  };
  (globalThis as { window?: unknown }).window = {
    localStorage: storage,
    location: { href: "http://localhost:3000/runs" },
  };
  (globalThis as { localStorage?: unknown }).localStorage = storage;
}

async function seedFullSession(): Promise<void> {
  saveSession("tok-abc", principal);
  setUserId(principal.userId);
  setProjectId("p-7");
  setRunId("r-3");
  setPrId("42");
  setGoal("ship the migration");
}

function stubFetchOnce(status: number, body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: status >= 200 && status < 300,
      status,
      text: async () => JSON.stringify(body),
    })),
  );
}

beforeEach(() => {
  installFakeBrowser();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete (globalThis as { window?: unknown }).window;
  delete (globalThis as { localStorage?: unknown }).localStorage;
});

describe("apiFetch 401 interceptor (H-003)", () => {
  it("on 401: clears token + all loose keys, redirects to /login?expired=1, throws ApiError", async () => {
    await seedFullSession();

    // Section 1's real 401 shape: HTTPException(detail="invalid or expired session").
    stubFetchOnce(401, { detail: "invalid or expired session" });

    await expect(getRunStatus("r-3", "tok-abc")).rejects.toBeInstanceOf(ApiError);

    expect(window.location.href).toBe("/login?expired=1");

    // Session hygiene: nothing usable survives the bounce.
    expect(loadSession()).toBeNull();
    expect(getToken()).toBeNull();
    expect(getUserId()).toBeNull();
    expect(getProjectId()).toBeNull();
    expect(getRunId()).toBeNull();
    expect(getPrId()).toBeNull();
    expect(getGoal()).toBeNull();
  });

  it("non-401 failures leave the session and location untouched", async () => {
    await seedFullSession();

    stubFetchOnce(429, {
      detail: { error: { code: "RATE_LIMITED", message: "too many attempts; slow down" } },
    });

    const err = (await getRunStatus("r-3", "tok-abc").catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(429);
    expect(err.code).toBe("RATE_LIMITED");

    expect(getToken()).toBe("tok-abc");
    expect(getProjectId()).toBe("p-7");
    expect(getRunId()).toBe("r-3");
    expect(window.location.href).toBe("http://localhost:3000/runs");
  });
});
