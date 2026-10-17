import { describe, expect, it } from "vitest";
import { HarnessClient, HarnessError, assertSafeBase, pendingHunks, redact } from "../src/core/protocol";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe("transport safety", () => {
  it("allows https and loopback http only; refuses embedded credentials", () => {
    expect(assertSafeBase("https://h.example.com/", "x")).toBe("https://h.example.com");
    expect(assertSafeBase("http://localhost:8001", "x")).toBe("http://localhost:8001");
    expect(assertSafeBase("http://127.0.0.1:8001", "x")).toBe("http://127.0.0.1:8001");
    for (const bad of ["http://h.example.com", "ftp://x", "not a url", "https://u:p@h.example.com"]) {
      expect(() => assertSafeBase(bad, "x")).toThrow(HarnessError);
    }
  });
  it("refuses an insecure endpoint at construction, before any token use", () => {
    expect(() => new HarnessClient({ identity: "http://evil.example.com" }, async () => "t")).toThrow(/https/);
  });
});

describe("redaction", () => {
  it("scrubs bearer headers and the literal token", () => {
    expect(redact("Authorization: Bearer abc.def-123456789", null)).toBe("Authorization: Bearer [REDACTED]");
    expect(redact("failed with tok_secret_value here", "tok_secret_value")).toBe("failed with [REDACTED] here");
  });
});

describe("HarnessClient", () => {
  const mk = (fetchImpl: typeof fetch, token = "tok-123456") =>
    new HarnessClient(
      { identity: "http://localhost:1", workflow: "http://localhost:2", orchestrator: "http://localhost:3" },
      async () => token, fetchImpl);

  it("sends bearer, never follows redirects, url-encodes ids", async () => {
    let seen: { url: string; init: RequestInit } | undefined;
    const c = mk((async (url: string, init: RequestInit) => { seen = { url, init }; return json({ status: "x" }); }) as unknown as typeof fetch);
    await c.runStatus("a/../b");
    expect(seen?.url).toBe("http://localhost:3/api/v1/orchestrator/runs/a%2F..%2Fb/status");
    expect((seen?.init.headers as Record<string, string>).authorization).toBe("Bearer tok-123456");
    expect(seen?.init.redirect).toBe("error");
  });
  it("fails closed with no token and for unconfigured services", async () => {
    await expect(mk((async () => json({})) as unknown as typeof fetch, "").me()).rejects.toMatchObject({ code: "UNAUTHORIZED" });
    const partial = new HarnessClient({ identity: "http://localhost:1" }, async () => "t", (async () => json({})) as unknown as typeof fetch);
    await expect(partial.runStatus("r")).rejects.toMatchObject({ code: "UNSUPPORTED" });
  });
  it("maps statuses to clear messages and the frozen error envelope", async () => {
    const err = (status: number, code = "X") => mk((async () => json({ error: { code, message: "m" } }, status)) as unknown as typeof fetch);
    await expect(err(401).me()).rejects.toThrow(/Sign in again/);
    await expect(err(403, "ROLE_DENIED").approvePlan("r", "p")).rejects.toThrow(/role/);
    await expect(err(409, "STALE_BASE").decide("c", [])).rejects.toThrow(/STALE_BASE/);
  });
  it("network failure messages never contain the token", async () => {
    const c = mk((async () => { throw new Error("connect ECONNREFUSED while sending tok-123456"); }) as unknown as typeof fetch);
    await expect(c.me()).rejects.toSatisfy((e: unknown) => !(e as Error).message.includes("tok-123456") && /Could not reach/.test((e as Error).message));
  });
  it("pendingHunks counts only undecided hunks", () => {
    const cs = { changeSetId: "c", title: "t", status: "open", files: [{ path: "a", hunks: [{ id: "h0", lines: [] }, { id: "h1", lines: [] }], decisions: { h0: { decision: "accept" } } }] };
    expect(pendingHunks(cs)).toBe(1);
  });
});
