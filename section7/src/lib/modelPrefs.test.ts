// Team model prefs client — pure helpers and the wire contract.
// The select options and the composer chip both derive from providerGroups /
// shortModel, so those are tested directly; fetch is stubbed (node env, no
// jsdom) to pin the URL, method, bearer header and error envelope.
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  MODELS_BASE,
  REASONING_EFFORTS,
  canEditModelPrefs,
  fetchModelPrefs,
  providerGroups,
  saveModelPrefs,
  scopeNote,
  shortModel,
  type CatalogProvider,
} from "./modelPrefs";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(responses: Array<{ status: number; body?: unknown }>) {
  const queue = [...responses];
  return vi.fn(async () => {
    const next = queue.shift() ?? { status: 500 };
    return {
      ok: next.status >= 200 && next.status < 300,
      status: next.status,
      text: async () => (next.body === undefined ? "" : JSON.stringify(next.body)),
    };
  });
}

const catalog: CatalogProvider[] = [
  { name: "deepseek", kind: "openai-compatible", models: ["deepseek/deepseek-chat", "deepseek/deepseek-reasoner"] },
  { name: "empty", kind: "openai-compatible", models: [] },
];

describe("providerGroups", () => {
  it("groups by provider, drops empty providers and keeps saved choices visible", () => {
    const groups = providerGroups(catalog, ["deepseek/deepseek-reasoner", "retired/model-x"]);
    expect(groups).toEqual([
      { name: "deepseek", models: ["deepseek/deepseek-chat", "deepseek/deepseek-reasoner"] },
      { name: "current", models: ["retired/model-x"] },
    ]);
  });

  it("adds no 'current' group when every saved model is in the catalog", () => {
    const groups = providerGroups(catalog, ["deepseek/deepseek-chat"]);
    expect(groups.map((g) => g.name)).toEqual(["deepseek"]);
  });

  it("tolerates a failed catalog load (empty providers)", () => {
    expect(providerGroups([], ["deepseek/deepseek-chat"])).toEqual([
      { name: "current", models: ["deepseek/deepseek-chat"] },
    ]);
  });
});

describe("shortModel / scopeNote / canEditModelPrefs", () => {
  it("strips the provider prefix and truncates long ids", () => {
    expect(shortModel("deepseek/deepseek-reasoner")).toBe("deepseek-reasoner");
    expect(shortModel("deepseek-chat")).toBe("deepseek-chat");
    const long = `provider/${"x".repeat(40)}`;
    expect(shortModel(long).length).toBe(28);
    expect(shortModel(long).endsWith("…")).toBe(true);
  });

  it("says whether reasoning effort actually reaches the provider", () => {
    expect(scopeNote(true)).toContain("Reasoning effort is sent to the provider.");
    expect(scopeNote(false)).toContain("does not accept it");
  });

  it("only team admins may change the defaults", () => {
    expect(canEditModelPrefs("admin")).toBe(true);
    expect(canEditModelPrefs("engineer")).toBe(false);
    expect(canEditModelPrefs(null)).toBe(false);
    expect(canEditModelPrefs(undefined)).toBe(false);
  });

  it("offers the four stored effort levels", () => {
    expect(REASONING_EFFORTS).toEqual(["off", "low", "medium", "high"]);
  });
});

describe("wire contract", () => {
  const prefs = {
    teamId: "team-1",
    primaryModel: "deepseek/deepseek-chat",
    reviewerModel: "deepseek/deepseek-reasoner",
    reasoningEffort: "medium" as const,
    persisted: true,
    reasoningWireSupported: false,
    updatedAt: "2026-10-25T00:00:00Z",
  };

  it("GETs prefs with the bearer token", async () => {
    const f = stubFetch([{ status: 200, body: prefs }]);
    vi.stubGlobal("fetch", f);
    await expect(fetchModelPrefs("tok")).resolves.toEqual(prefs);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${MODELS_BASE}/api/v1/models/prefs`);
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer tok");
  });

  it("PUTs the three editable fields (teamId and flags are server-owned)", async () => {
    const f = stubFetch([{ status: 200, body: prefs }]);
    vi.stubGlobal("fetch", f);
    await saveModelPrefs("tok", {
      primaryModel: prefs.primaryModel,
      reviewerModel: prefs.reviewerModel,
      reasoningEffort: "high",
    });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${MODELS_BASE}/api/v1/models/prefs`);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({
      primaryModel: prefs.primaryModel,
      reviewerModel: prefs.reviewerModel,
      reasoningEffort: "high",
    });
  });

  it("surfaces UNKNOWN_MODEL from a rejected save", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch([
        {
          status: 400,
          body: { error: { code: "UNKNOWN_MODEL", message: "unknown model: nope" } },
        },
      ]),
    );
    try {
      await saveModelPrefs("tok", {
        primaryModel: "nope",
        reviewerModel: prefs.reviewerModel,
        reasoningEffort: "off",
      });
      expect.fail("expected ApiError");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).code).toBe("UNKNOWN_MODEL");
      expect((e as ApiError).status).toBe(400);
    }
  });
});
