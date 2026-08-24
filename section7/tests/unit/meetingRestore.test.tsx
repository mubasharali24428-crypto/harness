// M12-E6 — restore path. Node-env constraint (no jsdom/RTL here, installs out
// of scope): following the repo's planGate pattern — the restore ORCHESTRATION
// lives in src/lib/session.ts as pure functions exercised directly with
// injected S6 GET fakes; the page's mount effect is verified as its thin
// caller (correct team/token handed over, existing GET endpoints reused,
// stale-pointer sweep observable in storage). renderToStaticMarkup never runs
// effects, so rendered-output claims are limited to what SSR can honestly show.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
// Classic JSX transform under vitest/esbuild: React must be in scope.
import { renderToStaticMarkup } from "react-dom/server";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const getMessages = vi.fn();
const getBrief = vi.fn();
const createMeeting = vi.fn();

// AppShell is shared chrome outside this lane's owned paths and pulls the Next
// router; the behavior under test lives in lib/page, so swap the shell for a
// pass-through.
vi.mock("@/components/AppShell", () => ({
  default: (props: { children?: React.ReactNode }) =>
    React.createElement("div", null, props.children),
}));

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
  getMessages.mockReset();
  getBrief.mockReset();
  createMeeting.mockReset();

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

  // Signed-in session whose principal carries teamId t-1; active project p-1.
  window.localStorage.setItem(
    "harness.session",
    JSON.stringify({ token: "tok", principal: { userId: "u-1", teamId: "t-1", role: "admin" } }),
  );
  window.localStorage.setItem("harness.projectId", "p-1");
});

afterEach(() => {
  delete (globalThis as { window?: unknown }).window;
  delete (globalThis as { localStorage?: unknown }).localStorage;
});

function savedRecord(meetingId: string, projectId: string | null): string {
  return JSON.stringify({ meetingId, projectId, savedAt: "2026-08-23T00:00:00Z" });
}

describe("restore orchestration (src/lib/session.ts)", () => {
  it("restores the persisted meeting through the EXISTING GET messages endpoint", async () => {
    window.localStorage.setItem("harness.meeting.t-1", savedRecord("m-live-9", "p-1"));
    getMessages.mockResolvedValue([{ userId: "u-1", content: "hello team", isAiGenerated: false }]);
    getBrief.mockRejectedValue(Object.assign(new Error("404"), { status: 404 }));

    const { restoreLatestMeeting } = await import("@/lib/session");
    const result = await restoreLatestMeeting("t-1", "p-1", "tok", getMessages, getBrief);

    expect(getMessages).toHaveBeenCalledTimes(1);
    expect(getMessages).toHaveBeenCalledWith("m-live-9", "tok"); // rehydrated via GET messages
    expect(createMeeting).not.toHaveBeenCalled(); // restore mints nothing
    expect(result?.outcome).toBe("ok");
    expect(result?.meetingId).toBe("m-live-9");
    expect(result?.messages).toHaveLength(1);
    expect(result?.messages[0]).toMatchObject({ content: "hello team" });
    expect(result?.brief).toBeNull(); // brief 404 pre-generation is tolerated
  });

  it("carries an existing brief back with the transcript", async () => {
    window.localStorage.setItem("harness.meeting.t-1", savedRecord("m-brief", "p-1"));
    getMessages.mockResolvedValue([{ userId: "u-1", content: "discussing", isAiGenerated: false }]);
    getBrief.mockResolvedValue({
      id: "b-1",
      status: "draft",
      requirements: ["ship it"],
      constraints: [],
      acceptanceCriteria: [],
      risks: [],
    });

    const { restoreLatestMeeting } = await import("@/lib/session");
    const result = await restoreLatestMeeting("t-1", "p-1", "tok", getMessages, getBrief);

    expect(result?.outcome).toBe("ok");
    expect(result?.brief?.id).toBe("b-1");
  });

  it("returns null when nothing is persisted — no endpoints touched, nothing created", async () => {
    const { restoreLatestMeeting } = await import("@/lib/session");
    const result = await restoreLatestMeeting("t-1", "p-1", "tok", getMessages, getBrief);

    expect(result).toBeNull();
    expect(getMessages).not.toHaveBeenCalled();
    expect(getBrief).not.toHaveBeenCalled();
    expect(createMeeting).not.toHaveBeenCalled();
  });

  it("server 404 → outcome 'missing': caller sweeps the stale pointer", async () => {
    window.localStorage.setItem("harness.meeting.t-1", savedRecord("m-deleted", "p-1"));
    // Shape matches what @/lib/api throws: ApiError carries .status === 404.
    getMessages.mockRejectedValue(Object.assign(new Error("Not Found"), { status: 404 }));

    const { restoreLatestMeeting } = await import("@/lib/session");
    const result = await restoreLatestMeeting("t-1", "p-1", "tok", getMessages);

    expect(result?.outcome).toBe("missing");
    expect(result?.meetingId).toBe("m-deleted"); // caller learns WHICH pointer died
  });

  it("network failure → outcome 'unreachable': pointer survives, claims nothing", async () => {
    window.localStorage.setItem("harness.meeting.t-1", savedRecord("m-alive", "p-1"));
    getMessages.mockRejectedValue(new Error("Network Error"));

    const { restoreLatestMeeting } = await import("@/lib/session");
    const result = await restoreLatestMeeting("t-1", "p-1", "tok", getMessages);

    expect(result?.outcome).toBe("unreachable");
    expect(store.has("harness.meeting.t-1")).toBe(true); // orchestration never destroys data
  });

  it("refuses a pointer recorded against a different project (cross-workspace)", async () => {
    window.localStorage.setItem("harness.meeting.t-1", savedRecord("m-other", "p-OTHER"));

    const { restoreLatestMeeting } = await import("@/lib/session");
    const result = await restoreLatestMeeting("t-1", "p-1", "tok", getMessages);

    expect(result).toBeNull();
    expect(getMessages).not.toHaveBeenCalled();
    // And it does NOT destroy the record either — another session on p-OTHER
    // sharing this browser may legitimately come back for it.
    expect(store.has("harness.meeting.t-1")).toBe(true);
  });
});

describe("page disposition via runMeetingRestore (effect body, no DOM needed)", () => {
  /** Recording stand-ins for the page's setState functions. */
  function makeDeps(overrides?: Partial<Record<string, unknown>>) {
    const state = {
      meetingId: null as string | null,
      messages: [] as unknown[],
      live: false,
      brief: null as unknown,
      restored: false,
      error: null as string | null,
    };
    const deps = {
      teamId: "t-1",
      token: "tok",
      getProjectId: () => "p-1",
      getMessages,
      getBrief,
      setMeetingId: (id: string | null) => {
        state.meetingId = id;
      },
      setMessages: (msgs: unknown[]) => {
        state.messages = msgs;
      },
      setLive: (v: boolean) => {
        state.live = v;
      },
      setBrief: (b: unknown) => {
        state.brief = b;
      },
      setRestoredFromStorage: (v: boolean) => {
        state.restored = v;
      },
      setError: (msg: string | null) => {
        state.error = msg;
      },
      ...overrides,
    };
    return { deps: deps as never, raw: deps, state };
  }

  /** act() without jsdom — just flushes the pending work. */
  async function actAsync(fn: () => Promise<void>): Promise<void> {
    await fn();
    await Promise.resolve();
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  }

  it("happy path: meetingId set, transcript landed, restored flag raised, nothing minted", async () => {
    window.localStorage.setItem("harness.meeting.t-1", savedRecord("m-live-9", "p-1"));
    const msgs = [
      { userId: "u-1", content: "hello team", isAiGenerated: false },
      { userId: "ai-participant", content: "on it", isAiGenerated: true },
    ];
    getMessages.mockResolvedValue(msgs);
    getBrief.mockResolvedValue({
      id: "b-9",
      status: "draft",
      requirements: ["r"],
      constraints: [],
      acceptanceCriteria: [],
      risks: [],
    });

    const { runMeetingRestore } = await import("@/lib/session");
    const { deps, state } = makeDeps();
    await actAsync(async () => {
      runMeetingRestore(deps);
    });

    // Existing GET endpoints carried the rehydration — no new surface.
    expect(getMessages).toHaveBeenCalledWith("m-live-9", "tok");
    expect(getBrief).toHaveBeenCalledWith("m-live-9", "tok");
    expect(state.meetingId).toBe("m-live-9");
    expect(state.messages).toEqual(msgs); // chat rehydrated
    expect(state.live).toBe(true);
    expect(state.restored).toBe(true); // driver of the visible "meeting restored" note
    expect(state.brief).toMatchObject({ id: "b-9" });
    expect(createMeeting).not.toHaveBeenCalled(); // restore mints NOTHING
  });

  it("nothing persisted → every setter untouched, endpoints untouched", async () => {
    const { runMeetingRestore } = await import("@/lib/session");
    const { deps, state } = makeDeps();
    await actAsync(async () => {
      runMeetingRestore(deps);
    });

    expect(getMessages).not.toHaveBeenCalled();
    expect(createMeeting).not.toHaveBeenCalled();
    expect(state.meetingId).toBeNull();
    expect(state.error).toBeNull(); // silent idle — not an error condition
  });

  it("'missing': stale pointer swept from storage, user told to start fresh", async () => {
    window.localStorage.setItem("harness.meeting.t-1", savedRecord("m-deleted", "p-1"));
    getMessages.mockRejectedValue(Object.assign(new Error("Not Found"), { status: 404 }));

    const { runMeetingRestore } = await import("@/lib/session");
    const { deps, state } = makeDeps();
    await actAsync(async () => {
      runMeetingRestore(deps);
    });

    expect(store.has("harness.meeting.t-1")).toBe(false); // pointer swept
    expect(state.meetingId).toBeNull(); // back to clean idle UI
    expect(state.error).toContain("no longer exists");
    expect(state.restored).toBe(false);
    expect(createMeeting).not.toHaveBeenCalled(); // still no auto-create
  });

  it("'unreachable': pointer kept for retry, but restoration is NOT claimed", async () => {
    window.localStorage.setItem("harness.meeting.t-1", savedRecord("m-alive", "p-1"));
    getMessages.mockRejectedValue(new Error("Network Error"));

    const { runMeetingRestore } = await import("@/lib/session");
    const { deps, state } = makeDeps();
    await actAsync(async () => {
      runMeetingRestore(deps);
    });

    expect(store.has("harness.meeting.t-1")).toBe(true); // survives the outage
    expect(state.meetingId).toBe("m-alive");
    expect(state.restored).toBe(false); // no false "restored" banner
    expect(state.error).toContain("couldn't be loaded");
  });

  it("no team or no token → restore is a no-op", async () => {
    const { runMeetingRestore } = await import("@/lib/session");

    const a = makeDeps({ teamId: null });
    runMeetingRestore(a.deps);

    const b = makeDeps({ token: null });
    runMeetingRestore(b.deps);

    await actAsync(async () => {});

    expect(getMessages).not.toHaveBeenCalled();
    expect(a.state.meetingId).toBeNull();
    expect(b.state.meetingId).toBeNull();
  });
});

describe("visible surface pins", () => {
  it("banner lead copy is pinned and consumed by the page", async () => {
    const { RESTORED_NOTE_LEAD } = await import("@/lib/session");
    expect(RESTORED_NOTE_LEAD).toBe("Meeting restored");
    const fs = await import("node:fs");
    const src = await fs.promises.readFile(
      new URL("../../app/meeting/page.tsx", import.meta.url),
      "utf8",
    );
    expect(src).toContain('data-testid="meeting-restored-note"');
    expect(src).toContain("RESTORED_NOTE_LEAD");
    // Pill shows the restored state distinctly while it lasts.
    expect(src).toContain('"restored"');
  });

  it("SSR skeleton stays idle when nothing is persisted (honest empty state)", async () => {
    const { default: Meeting } = await import("../../app/meeting/page");
    let html = "";
    await act(async () => {
      html = renderToStaticMarkup(React.createElement(Meeting));
    });
    expect(html).toContain("Start a live meeting");
    expect(html).toContain("Nothing yet");

    // Local act alias for the flush above — keeps react-dom/test-utils out.
    async function act(fn: () => void): Promise<void> {
      await fn();
    }
  });
});
