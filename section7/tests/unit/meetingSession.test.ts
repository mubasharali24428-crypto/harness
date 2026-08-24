// M12-E6 — persistence round-trip: saveMeeting → loadMeeting per team, latest
// wins, corrupt payloads read as null, and SSR/no-window safety. Node-env
// constraint (no jsdom in this repo): Storage is the same hand-rolled
// spec-compliant mock used by tests/unit/sessionWipe.test.ts.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  clearMeeting,
  loadMeeting,
  resolveRestorableMeeting,
  saveMeeting,
  type PersistedMeeting,
} from "@/lib/session";

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
  (globalThis as { window?: unknown }).window = { localStorage: storage };
  (globalThis as { localStorage?: unknown }).localStorage = storage;
});

afterEach(() => {
  delete (globalThis as { window?: unknown }).window;
  delete (globalThis as { localStorage?: unknown }).localStorage;
});

describe("meeting persistence round-trip", () => {
  it("saves and loads a meeting under harness.meeting.<teamId>", () => {
    saveMeeting("t-1", "m-abc-123", "p-7");
    expect(store.has("harness.meeting.t-1")).toBe(true);

    const got = loadMeeting("t-1");
    expect(got).not.toBeNull();
    expect(got?.meetingId).toBe("m-abc-123");
    expect(got?.projectId).toBe("p-7");
    expect(typeof got?.savedAt).toBe("string");
    // savedAt must be a real ISO timestamp, not a placeholder
    expect(Number.isNaN(Date.parse(got?.savedAt ?? ""))).toBe(false);
  });

  it("keeps teams isolated — one record each, never cross-contaminated", () => {
    saveMeeting("t-1", "m-team-one", "p-1");
    saveMeeting("t-2", "m-team-two", "p-2");

    expect(loadMeeting("t-1")?.meetingId).toBe("m-team-one");
    expect(loadMeeting("t-2")?.meetingId).toBe("m-team-two");
    expect(store.size).toBe(2);
  });

  it("latest meeting wins when the same team saves again", () => {
    saveMeeting("t-1", "m-first", "p-1");
    saveMeeting("t-1", "m-second", "p-1");

    const got = loadMeeting("t-1");
    expect(got?.meetingId).toBe("m-second");
    expect(store.size).toBe(1); // replaced, not appended
  });

  it("persists projectId: null cleanly and round-trips it back", () => {
    saveMeeting("t-3", "m-no-project", null);
    const got = loadMeeting("t-3");
    expect(got?.projectId).toBeNull();
  });

  it("reads malformed payloads as null instead of throwing", () => {
    saveMeeting("t-1", "m-ok", "p-1"); // establish the shape…
    const good = JSON.parse(store.get("harness.meeting.t-1") as string);
    expect(good.meetingId).toBe("m-ok");

    store.set("harness.meeting.t-broken-json", "{not json at all");
    expect(loadMeeting("t-broken-json")).toBeNull();

    store.set("harness.meeting.t-wrong-shape", JSON.stringify([1, 2, 3]));
    expect(loadMeeting("t-wrong-shape")).toBeNull();

    store.set(
      "harness.meeting.t-empty-id",
      JSON.stringify({ meetingId: "", projectId: "p-1", savedAt: "x" }),
    );
    expect(loadMeeting("t-empty-id")).toBeNull();

    store.set("harness.meeting.t-nullish", "null");
    expect(loadMeeting("t-nullish")).toBeNull();
  });

  it("clearMeeting drops only that team's record", () => {
    saveMeeting("t-1", "m-a", "p-1");
    saveMeeting("t-2", "m-b", "p-2");

    clearMeeting("t-1");

    expect(loadMeeting("t-1")).toBeNull();
    expect(loadMeeting("t-2")?.meetingId).toBe("m-b");
  });

  it("resolveRestorableMeeting refuses cross-workspace resurrection", () => {
    saveMeeting("t-1", "m-old", "p-old");
    // Active session is on a different project than the one saved.
    expect(resolveRestorableMeeting("t-1", "p-other")).toBeNull();
    // Matching project restores fine.
    expect(resolveRestorableMeeting("t-1", "p-old")?.meetingId).toBe("m-old");
  });

  it("resolveRestorableMeeting handles absent team / legacy records", () => {
    saveMeeting("t-1", "m-x", null); // legacy record without a project id
    expect(resolveRestorableMeeting(null, "p-1")).toBeNull(); // no team → nothing
    expect(resolveRestorableMeeting("t-1", "p-whatever")?.meetingId).toBe("m-x");
    expect(resolveRestorableMeeting("t-missing", "p-1")).toBeNull();
  });

  it("typed shape matches what the page consumes", () => {
    saveMeeting("t-typed", "m-9", "p-9");
    const rec: PersistedMeeting | null = loadMeeting("t-typed");
    const ids: string[] = [];
    if (rec) ids.push(rec.meetingId);
    expect(ids).toEqual(["m-9"]);
  });
});
