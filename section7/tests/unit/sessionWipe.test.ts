import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { Principal } from "@contracts/roles";
import {
  KEYS,
  clearSession,
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

// M12-E2 sign-out hygiene: clearSession() must wipe the session token AND
// every loose harness.* key (projectId, runId, prId, goal included) so nothing
// leaks into the next account on a shared browser.

const principal: Principal = { userId: "u-1", teamId: "t-1", role: "admin" };

function makeStorage(): { storage: Storage; store: Map<string, string> } {
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
  return { storage, store };
}

let store: Map<string, string>;

beforeEach(() => {
  const { storage, store: s } = makeStorage();
  store = s;
  (globalThis as { window?: unknown }).window = {
    localStorage: storage,
    location: { href: "" },
  };
  (globalThis as { localStorage?: unknown }).localStorage = storage;
});

afterEach(() => {
  delete (globalThis as { window?: unknown }).window;
  delete (globalThis as { localStorage?: unknown }).localStorage;
});

describe("clearSession wipes all harness.* state", () => {
  it("removes token + projectId/runId/prId/goal/userId and any other harness.* key", () => {
    saveSession("tok-abc", principal);
    setUserId("u-1");
    setProjectId("p-7");
    setRunId("r-3");
    setPrId("42");
    setGoal("ship the migration");
    // A future loose key added by some page must be swept too — prefix sweep.
    store.set("harness.someFutureKey", "x");
    // Non-harness keys are none of sign-out's business.
    store.set("theme", "dark");

    clearSession();

    expect(store.size).toBe(1);
    expect(store.has("theme")).toBe(true);
    expect(loadSession()).toBeNull();
    expect(getToken()).toBeNull();
    expect(getUserId()).toBeNull();
    expect(getProjectId()).toBeNull();
    expect(getRunId()).toBeNull();
    expect(getPrId()).toBeNull();
    expect(getGoal()).toBeNull();
    for (const value of Object.values(KEYS)) {
      expect(store.has(value)).toBe(false);
    }
  });
});
