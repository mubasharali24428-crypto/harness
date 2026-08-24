// M12-E7 (raw-04 M1) — vitest for the onboarding auto-complete derivation.
// Pure-function tests: mock API states (what GET /github/connection, /keys,
// /orchestrator/runs, /teams/{id}/members return) go in, done-flags come out.
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChecklistItemDto } from "./onboardingApi";
import {
  EMPTY_SIGNALS,
  activeMilestones,
  deriveItemDone,
  deriveItems,
  fetchGithubConnected,
  fetchKeysCount,
  fetchMembersCount,
  fetchRunsTotal,
  stepActionHref,
  stepActionLabel,
} from "./onboardingDerived";

afterEach(() => {
  vi.unstubAllGlobals();
});

const ITEM = (
  id: string,
  serverDone = false,
  title = id,
): ChecklistItemDto => ({ id, title, description: null, done: serverDone });

const ALL_FALSE: ChecklistItemDto[] = [
  ITEM("connect_github"),
  ITEM("create_team"),
  ITEM("invite_members"),
  ITEM("api_key_added"),
  ITEM("first_run"),
];

describe("deriveItemDone — per-step real-world conditions", () => {
  it("team created ⇔ principal has a team", () => {
    expect(deriveItemDone("create_team", { ...EMPTY_SIGNALS, teamExists: true }, false)).toBe(true);
    expect(deriveItemDone("create_team", { ...EMPTY_SIGNALS, teamExists: false }, false)).toBe(false);
    // Unknown signal → honest fallback to the server verdict, never guessed.
    expect(deriveItemDone("create_team", EMPTY_SIGNALS, true)).toBe(true);
    expect(deriveItemDone("create_team", EMPTY_SIGNALS, false)).toBe(false);
  });

  it("members invited ⇔ members list has more than one member", () => {
    expect(deriveItemDone("invite_members", { ...EMPTY_SIGNALS, membersCount: 2 }, false)).toBe(true);
    expect(deriveItemDone("invite_members", { ...EMPTY_SIGNALS, membersCount: 5 }, false)).toBe(true);
    expect(deriveItemDone("invite_members", { ...EMPTY_SIGNALS, membersCount: 1 }, false)).toBe(false);
    expect(deriveItemDone("invite_members", { ...EMPTY_SIGNALS, membersCount: 0 }, false)).toBe(false);
    expect(deriveItemDone("invite_members", { ...EMPTY_SIGNALS, membersCount: null }, false)).toBe(false);
  });

  it("github connected ⇔ connection probe reports connected:true", () => {
    expect(deriveItemDone("connect_github", { ...EMPTY_SIGNALS, githubConnected: true }, false)).toBe(true);
    expect(deriveItemDone("connect_github", { ...EMPTY_SIGNALS, githubConnected: false }, false)).toBe(false);
    expect(deriveItemDone("connect_github", EMPTY_SIGNALS, true)).toBe(true); // fallback
  });

  it("API key added ⇔ keys list non-empty", () => {
    expect(deriveItemDone("api_key_added", { ...EMPTY_SIGNALS, keysCount: 0 }, false)).toBe(false);
    expect(deriveItemDone("api_key_added", { ...EMPTY_SIGNALS, keysCount: 3 }, false)).toBe(true);
    expect(deriveItemDone("api_key_added", EMPTY_SIGNALS, false)).toBe(false);
  });

  it("first run ⇔ runs index total > 0", () => {
    expect(deriveItemDone("first_run", { ...EMPTY_SIGNALS, runsTotal: 0 }, false)).toBe(false);
    expect(deriveItemDone("first_run", { ...EMPTY_SIGNALS, runsTotal: 42 }, false)).toBe(true);
    expect(deriveItemDone("first_run", EMPTY_SIGNALS, true)).toBe(true); // fallback
  });

  it("backend-owned steps keep the server verdict; unknown ids never flip", () => {
    // Section-10-derived rows (guided_demo ack, informational other_integrations).
    expect(deriveItemDone("guided_demo", { ...EMPTY_SIGNALS, runsTotal: 9 }, false)).toBe(false);
    expect(deriveItemDone("guided_demo", { ...EMPTY_SIGNALS, runsTotal: 9 }, true)).toBe(true);
    expect(deriveItemDone("other_integrations", EMPTY_SIGNALS, true)).toBe(true);
    expect(deriveItemDone("no_such_step", { ...EMPTY_SIGNALS, keysCount: 4 }, false)).toBe(false);
  });
});

describe("deriveItems — mapping over server rows", () => {
  it("flips exactly the steps whose conditions hold and preserves order/shape", () => {
    const signals = {
      teamExists: true,
      membersCount: 2,
      githubConnected: false,
      keysCount: 0,
      runsTotal: 7,
    };
    const out = deriveItems(ALL_FALSE, signals);
    expect(out.map((i) => i.id)).toEqual(ALL_FALSE.map((i) => i.id)); // order intact
    expect(Object.fromEntries(out.map((i) => [i.id, i.done]))).toEqual({
      connect_github: false,
      create_team: true,
      invite_members: true,
      api_key_added: false,
      first_run: true,
    });
  });

  it("all-unknown signals leave every derived step at its server verdict", () => {
    const out = deriveItems(
      [...ALL_FALSE, ITEM("guided_demo", true)],
      EMPTY_SIGNALS,
    );
    expect(out.every((i, idx) => i.done === (idx === 5))).toBe(true);
  });

  it("does not mutate the input array or share row objects", () => {
    const input = ALL_FALSE.map((i) => ({ ...i }));
    const snapshot = JSON.stringify(input);
    deriveItems(input, { ...EMPTY_SIGNALS, keysCount: 2, githubConnected: true });
    expect(JSON.stringify(input)).toBe(snapshot);
  });
});

describe("activeMilestones — extra verifiable first-run goals", () => {
  it("empty when no signal is positive; unknown counts as not reached", () => {
    expect(activeMilestones(EMPTY_SIGNALS)).toEqual([]);
    expect(activeMilestones({ ...EMPTY_SIGNALS, keysCount: 0, runsTotal: 0 })).toEqual([]);
  });

  it("reports milestones in stable order when their conditions exist", () => {
    const m = activeMilestones({ ...EMPTY_SIGNALS, keysCount: 1, runsTotal: 3 });
    expect(m).toEqual(["api_key_added", "first_run"]);
    expect(activeMilestones({ ...EMPTY_SIGNALS, runsTotal: 3 })).toEqual(["first_run"]);
  });
});

describe("step deep links (raw-04 M1: each item links where the work happens)", () => {
  it("maps known ids to their owning surfaces", () => {
    expect(stepActionHref("connect_github")).toBe("/settings");
    expect(stepActionHref("create_team")).toBe("/settings/team");
    expect(stepActionHref("invite_members")).toBe("/settings/team");
    expect(stepActionHref("api_key_added")).toBe("/settings/keys");
    expect(stepActionHref("start_meeting")).toBe("/meeting");
    expect(stepActionHref("first_run")).toBe("/");
  });

  it("informational/backend-owned steps carry no app link; labels stay non-empty", () => {
    expect(stepActionHref("guided_demo")).toBeNull();
    expect(stepActionHref("other_integrations")).toBeNull();
    expect(stepActionHref("unknown_id")).toBeNull();
    for (const id of ["connect_github", "create_team", "invite_members", "api_key_added", "start_meeting", "first_run"]) {
      expect(stepActionLabel(id)).toBeTruthy();
    }
  });
});

// ---------------------------------------------------------------------------
// Signal probes — shape guards against the four live endpoints. Any failure
// mode must resolve to null (never throw, never read as progress).
// ---------------------------------------------------------------------------

function stubJson(status: number, body: unknown) {
  return vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  }));
}

function stubText(status: number, text: string) {
  return vi.fn(async () => ({ ok: status >= 200 && status < 300, status, text }));
}

const TOKEN = "tok-e7";

describe("fetchGithubConnected", () => {
  it("reads connected from GET /api/v1/github/connection with bearer", async () => {
    const f = stubJson(200, { connected: true, login: "octo", scopes: ["repo"] });
    vi.stubGlobal("fetch", f);
    await expect(fetchGithubConnected(TOKEN)).resolves.toBe(true);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/api/v1/github/connection");
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("non-2xx, malformed bodies and network failure resolve to null", async () => {
    vi.stubGlobal("fetch", stubJson(500, { connected: true })); // even a lying 500
    await expect(fetchGithubConnected(null)).resolves.toBeNull();
    vi.stubGlobal("fetch", stubText(200, "<html>not json</html>"));
    await expect(fetchGithubConnected(null)).resolves.toBeNull();
    vi.stubGlobal("fetch", stubText(200, '{"connected":"yes"}')); // wrong type
    await expect(fetchGithubConnected(null)).resolves.toBeNull();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );
    await expect(fetchGithubConnected(null)).resolves.toBeNull();
  });
});

describe("fetchKeysCount / fetchRunsTotal / fetchMembersCount", () => {
  it("counts items of GET /keys?teamId= (contract M10-C1)", async () => {
    const f = stubJson(200, { items: [{ id: "k1" }, { id: "k2" }] });
    vi.stubGlobal("fetch", f);
    await expect(fetchKeysCount("t-9", TOKEN)).resolves.toBe(2);
    const [url] = f.mock.calls[0] as unknown as [string];
    expect(url).toContain("/api/v1/keys?teamId=t-9");
    await expect(fetchKeysCount(undefined, TOKEN)).resolves.toBe(2); // same endpoint body, unscoped URL
  });

  it("reads total from the M10-C2 runs page", async () => {
    const f = stubJson(200, { items: [], total: 12, limit: 1, offset: 0 });
    vi.stubGlobal("fetch", f);
    await expect(fetchRunsTotal(TOKEN)).resolves.toBe(12);
    const [url] = f.mock.calls[0] as unknown as [string];
    expect(url).toContain("/api/v1/orchestrator/runs");
    vi.stubGlobal("fetch", stubJson(200, { items: [] })); // drifted body, no total
    await expect(fetchRunsTotal(TOKEN)).resolves.toBeNull();
  });

  it("counts members of GET /teams/{id}/members (W2-C1 surface)", async () => {
    const f = stubJson(200, {
      items: [{ id: "u1" }, { id: "u2" }, { id: "u3" }],
    });
    vi.stubGlobal("fetch", f);
    await expect(fetchMembersCount("t/9", TOKEN)).resolves.toBe(3);
    const [url] = f.mock.calls[0] as unknown as [string];
    expect(url).toContain(`/api/v1/teams/${encodeURIComponent("t/9")}/members`);
  });

  it("error statuses resolve to null for all three probes", async () => {
    vi.stubGlobal("fetch", stubJson(404, { error: { code: "NOT_FOUND", message: "x" } }));
    await expect(fetchKeysCount("t", TOKEN)).resolves.toBeNull();
    await expect(fetchRunsTotal(TOKEN)).resolves.toBeNull();
    await expect(fetchMembersCount("t", TOKEN)).resolves.toBeNull();
  });
});
