// M19-N4 — canned-demo client tests. Pins the wire contract with Section 4's
// DemoSeedResponse (field-by-field, including the always-true replay flag),
// error propagation, and the landing-href helper.
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { DEMO_BASE, demoRunHref, demoSeedUrl, seedDemo } from "./demoApi";

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

function stubError(status: number, body: unknown) {
  return vi.fn(async () => ({
    ok: false,
    status,
    text: async () => JSON.stringify(body),
  }));
}

const TOKEN = "tok-demo";
const SEED_URL = `${DEMO_BASE}/api/v1/orchestrator/demo/seed`;

const SEEDED = {
  projectId: "demo-proj-abc123",
  projectName: "harness-demo-textstats",
  repoFullName: "local/demo-repo",
  runId: "demo-run-def456",
  state: "pr_opened",
  planId: "demo-plan-789",
  prId: "demo-pr-0aa",
  prUrl: "https://github.com/org/demo-repo/pull/42",
  costUsd: 0.000013,
  replay: true,
};

describe("canned-demo client — wire shape", () => {
  it("POSTs to /api/v1/orchestrator/demo/seed with bearer auth", async () => {
    const f = stubOk(SEEDED, 201);
    vi.stubGlobal("fetch", f);
    await seedDemo(TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(SEED_URL);
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).authorization).toBe(
      `Bearer ${TOKEN}`,
    );
  });

  it("returns the seeded graph verbatim (ids server-minted, replay=true)", async () => {
    const f = stubOk(SEEDED, 201);
    vi.stubGlobal("fetch", f);
    const res = await seedDemo(TOKEN);
    expect(res).toEqual(SEEDED);
    // The honesty law is structural: a response without replay:true does not
    // type as success — the client never normalizes it away.
    expect(res.replay).toBe(true);
    expect(res.runId.startsWith("demo-run-")).toBe(true);
  });

  it("maps ROLE_DENIED to an ApiError with code + status", async () => {
    vi.stubGlobal(
      "fetch",
      stubError(403, { detail: { error: { code: "ROLE_DENIED", message: "nope" } } }),
    );
    await expect(seedDemo(TOKEN)).rejects.toMatchObject({
      code: "ROLE_DENIED",
      status: 403,
    });
  });

  it("surfaces SERVICE_NOT_CONFIGURED (503) as an ApiError too", async () => {
    vi.stubGlobal(
      "fetch",
      stubError(503, {
        detail: { error: { code: "SERVICE_NOT_CONFIGURED", message: "not wired" } },
      }),
    );
    const err = seedDemo(TOKEN);
    await expect(err).rejects.toBeInstanceOf(ApiError);
  });
});

describe("demoLanding helpers", () => {
  it("builds the detail-page href for the seeded run", () => {
    expect(demoRunHref(SEEDED.runId, SEEDED.projectId)).toBe(
      `/projects/${SEEDED.projectId}/runs/${SEEDED.runId}`,
    );
  });
});
