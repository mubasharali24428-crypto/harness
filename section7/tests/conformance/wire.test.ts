/**
 * S7 conformance — golden fixtures pinning client calls to the frozen wire
 * contract (mirrors the backend sections' golden-fixture discipline).
 *
 * These fixtures are the SAME shapes the live stack returns (verified via curl
 * against S1/S6/S8 on 2026-08-22). If a backend change breaks this test, the
 * contract drifted — fix the backend or bump contracts, not this test.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EventEnvelope } from "@contracts/events";
import { fetchWsTicket, replayEvents, requestLoginOtp, verifyLoginOtp } from "@/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

const TOKEN = "tok-conf";

function okJson(body: unknown) {
  return async () => ({
    ok: true,
    status: 200,
    text: async () => JSON.stringify(body),
  });
}

// Golden envelope shape from contracts/events.py (frozen EventEnvelope).
const goldenEnvelope: EventEnvelope = {
  type: "plan_proposed",
  payload: { planId: "pln-1", runId: "run-1" },
  ts: "2026-08-22T00:00:00+00:00",
  seq: 27,
  projectId: "proj-1",
  runId: "run-1",
};

describe("S1 identity conformance", () => {
  it("replayEvents returns envelopes matching the frozen EventEnvelope shape", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(okJson({ items: [goldenEnvelope], nextCursor: 28 })),
    );
    const { items } = await replayEvents("proj-1", 0, TOKEN);
    expect(items).toHaveLength(1);
    const e = items[0];
    // Frozen keys — exactly these, no more, no fewer.
    expect(Object.keys(e).sort()).toEqual(
      ["projectId", "runId", "payload", "seq", "ts", "type"].sort(),
    );
    expect(typeof e.seq).toBe("number");
    expect(typeof e.ts).toBe("string");
  });

  it("ws-ticket returns the {ticket, expiresInSeconds} wire shape", async () => {
    const f = vi.fn(okJson({ ticket: "tkt-abc", expiresInSeconds: 30 }));
    vi.stubGlobal("fetch", f);
    const ticket = await fetchWsTicket("proj-1", TOKEN);
    expect(ticket).toBe("tkt-abc");
    const [, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ projectId: "proj-1" });
  });

  it("request-login is fire-and-forget {ok:true} (enumeration-safe)", async () => {
    const f = vi.fn(okJson({ ok: true }));
    vi.stubGlobal("fetch", f);
    await expect(requestLoginOtp("a@b.dev")).resolves.toBeUndefined();
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/api/v1/auth/request-login");
    expect(JSON.parse(String(init.body))).toEqual({ email: "a@b.dev" });
  });

  it("verify-otp returns {token, principal{userId,teamId,role}}", async () => {
    const principal = { userId: "u-1", teamId: "t-1", role: "admin" };
    const f = vi.fn(okJson({ token: "tok-9", principal }));
    vi.stubGlobal("fetch", f);
    const out = await verifyLoginOtp("a@b.dev", "123456");
    expect(out.token).toBe("tok-9");
    expect(Object.keys(out.principal).sort()).toEqual(["role", "teamId", "userId"]);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/api/v1/auth/verify-otp");
    expect(JSON.parse(String(init.body))).toEqual({ email: "a@b.dev", code: "123456" });
  });
});
