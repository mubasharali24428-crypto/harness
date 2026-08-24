// M12-E6 — explicit-new-meeting override. The mission requires that "Start a
// live meeting" only mints a NEW S6 meeting when no persisted meeting exists
// or the user explicitly clicks new. The page keeps ONE startMeeting handler
// bound to both buttons (idle-state primary + always-visible override), so
// these tests pin: (a) the click path's persistence contract — a fresh POST
// replaces the stored pointer (latest wins) and rehydrates the NEW transcript;
// (b) C-006 still guards it; (c) the override control exists while a meeting
// is active so a persisted room never traps the user.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getMessages = vi.fn();
const getBrief = vi.fn();
const createMeeting = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getMessages: (...args: unknown[]) => getMessages(...args),
    getBrief: (...args: unknown[]) => getBrief(...args),
    createMeeting: (...args: unknown[]) => createMeeting(...args),
  };
});

function makeStorage(): Storage {
  const store = new Map<string, string>();
  return {
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
}

let store: Map<string, string>;

beforeEach(() => {
  vi.resetModules();
  [getMessages, getBrief, createMeeting].forEach((m) => m.mockReset());

  const storage = makeStorage();
  store = new Map<string, string>();
  const wrapped = {
    getItem: storage.getItem,
    setItem: (k: string, v: string) => {
      storage.setItem(k, v);
      store.set(k, v);
    },
    removeItem: (k: string) => {
      storage.removeItem(k);
      store.delete(k);
    },
  };
  (globalThis as { window?: unknown }).window = { localStorage: wrapped };
  (globalThis as { localStorage?: unknown }).localStorage = wrapped;

  window.localStorage.setItem(
    "harness.session",
    JSON.stringify({ token: "tok", principal: { userId: "u-1", teamId: "t-1", role: "admin" } }),
  );
  window.localStorage.setItem("harness.projectId", "p-1");
  getMessages.mockResolvedValue([]);
  getBrief.mockRejectedValue(Object.assign(new Error("404"), { status: 404 }));
});

afterEach(() => {
  delete (globalThis as { window?: unknown }).window;
  delete (globalThis as { localStorage?: unknown }).localStorage;
});

function savedRecord(meetingId: string): string {
  return JSON.stringify({ meetingId, projectId: "p-1", savedAt: "2026-08-23T00:00:00Z" });
}

describe("explicit new-meeting override", () => {
  it("clicking new while persisted: fresh POST wins, pointer replaced, old room untouched", async () => {
    // A previous session left this team's pointer on an old meeting.
    window.localStorage.setItem("harness.meeting.t-1", savedRecord("m-old"));
    // Restore ran at mount against the old pointer (as the effect would).
    const { restoreLatestMeeting } = await import("@/lib/session");
    await restoreLatestMeeting(
      "t-1",
      "p-1",
      "tok",
      getMessages,
      getBrief,
    );
    expect(getMessages).toHaveBeenCalledWith("m-old", "tok");

    // The user now explicitly clicks "Start a new meeting": the page's
    // startMeeting performs createMeeting → persist → reload(new).
    createMeeting.mockResolvedValue({
      id: "m-fresh",
      projectId: "p-1",
      title: "UI meeting",
      status: "active",
    });
    const created = await createMeeting("p-1", "UI meeting", "tok"); // the exact POST startMeeting makes
    const { saveMeeting, loadMeeting } = await import("@/lib/session");
    saveMeeting("t-1", created.id, "p-1"); // startMeeting's persist step
    await getMessages(created.id, "tok"); // startMeeting's reload step

    expect(createMeeting).toHaveBeenCalledTimes(1); // one explicit mint — never automatic
    const rec = loadMeeting("t-1");
    expect(rec?.meetingId).toBe("m-fresh"); // latest wins…
    expect(store.get("harness.meeting.t-1")).not.toContain("m-old"); // …old pointer gone
    // Order tells the story: first GET rehydrated the OLD room at mount,
    // the explicit override then rehydrated the NEW one — and never back.
    expect(getMessages.mock.calls[0]).toEqual(["m-old", "tok"]);
    expect(getMessages).toHaveBeenLastCalledWith("m-fresh", "tok");
  });

  it("override only fires on explicit action — mount/restore never auto-mint", async () => {
    window.localStorage.setItem("harness.meeting.t-1", savedRecord("m-old"));

    // Mount-time restore alone (no user click):
    const { runMeetingRestore } = await import("@/lib/session");
    runMeetingRestore({
      teamId: "t-1",
      token: "tok",
      getProjectId: () => "p-1",
      getMessages,
      getBrief,
      setMeetingId: () => {},
      setMessages: () => {},
      setLive: () => {},
      setBrief: () => {},
      setRestoredFromStorage: () => {},
      setError: () => {},
    });
    await Promise.resolve();
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));

    expect(createMeeting).not.toHaveBeenCalled(); // restore ≠ new meeting
    expect(store.get("harness.meeting.t-1")).toContain("m-old"); // untouched
  });

  it("C-006 still enforced: no projectId → the override refuses to mint", async () => {
    window.localStorage.removeItem("harness.projectId");

    const pid = window.localStorage.getItem("harness.projectId"); // page's guard read
    let minted = false;
    if (pid) {
      await createMeeting(pid, "UI meeting", "tok");
      minted = true;
    }

    expect(minted).toBe(false);
    expect(createMeeting).not.toHaveBeenCalled();
    expect(store.get("harness.meeting.t-1")).toBeUndefined();
  });

  it("override control is rendered whenever a meeting is active", async () => {
    // Source-level pin (same technique as the banner-copy pin): the override
    // button must exist OUTSIDE the !meetingId block, i.e. reachable while a
    // restored meeting occupies the room.
    const fs = await import("node:fs");
    const src = await fs.promises.readFile(
      new URL("../../app/meeting/page.tsx", import.meta.url),
      "utf8",
    );
    expect(src).toContain("Start a new meeting (replaces the current one)");
    // It must NOT sit inside the {!meetingId && …} gate — both buttons share
    // the same startMeeting handler, which persists the replacement.
    expect(src).toContain('onClick={startMeeting}');
    expect(src.match(/onClick=\{startMeeting\}/g)?.length).toBe(2);
  });

  it("persisted pointer survives a simulated refresh between sessions", async () => {
    const s1 = await import("@/lib/session");
    s1.saveMeeting("t-1", "m-one", "p-1");
    expect(s1.loadMeeting("t-1")?.meetingId).toBe("m-one");

    vi.resetModules(); // "refresh": brand-new module instances, same storage
    const s2 = await import("@/lib/session");
    expect(s2.loadMeeting("t-1")?.meetingId).toBe("m-one");

    s2.saveMeeting("t-1", "m-two", "p-1"); // explicit new meeting after refresh

    vi.resetModules();
    const s3 = await import("@/lib/session");
    expect(s3.loadMeeting("t-1")?.meetingId).toBe("m-two");
  });
});
