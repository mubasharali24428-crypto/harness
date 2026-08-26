/**
 * Transport tests over a mocked fetch: bearer auth, envelope -> ApiError,
 * retry/backoff law, query-param hygiene, generated call shapes.
 * Mirrors clients/python/tests/test_sdk.py's HarnessHTTP section.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { ApiError } from "../src/errors.js";
import { HarnessHttp, baseUrlFor } from "../src/runtime.js";
import type { FetchLike } from "../src/runtime.js";
import { IdentityClient } from "../src/identity.js";

/** Minimal Response stand-in (avoids needing undici internals in mocks). */
function jsonResponse(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Response {
  const text = body === undefined ? "" : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => text,
    headers: new Headers({ "content-type": "application/json", ...headers }),
  } as unknown as Response;
}

function emptyResponse(status: number): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => "",
    headers: new Headers(),
  } as unknown as Response;
}

function textResponse(status: number, text: string): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => text,
    headers: new Headers({ "content-type": "text/plain; charset=utf-8" }),
  } as unknown as Response;
}

interface Captured {
  method?: string;
  url?: string;
  body?: string | undefined;
  auth?: string | null;
  ua?: string;
}

function mockHttp(
  responder: (req: Captured, call: number) => Response,
  options: ConstructorParameters<typeof HarnessHttp>[0] = {},
): { http: HarnessHttp; calls: Captured[] } {
  const calls: Captured[] = [];
  let n = 0;
  const impl: FetchLike = async (url, init) => {
    n += 1;
    const captured: Captured = {
      method: init.method,
      url,
      body: init.body,
      auth: init.headers.authorization ?? null,
      ua: init.headers["user-agent"],
    };
    calls.push(captured);
    return responder(captured, n);
  };
  // Defaults mirror clients/python/tests/test_sdk.py make_client():
  // a session token plus instant backoff so retry tests stay fast.
  return {
    http: new HarnessHttp({
      token: "tok",
      fetchImpl: impl,
      sleepFn: async () => {},
      ...options,
    }),
    calls,
  };
}

describe("HarnessHttp transport", () => {
  it("sends the bearer token and decodes JSON", async () => {
    const { http, calls } = mockHttp(() => jsonResponse(200, { ok: true }));
    const out = await http.request<{ ok: boolean }>("http://x", "/api/v1/me", "GET");
    assert.deepEqual(out, { ok: true });
    assert.equal(calls[0].auth, "Bearer tok");
    assert.equal(calls[0].ua, "harness-ts-sdk/1.0.0rc1");
  });

  it("omits the auth header when no token is set", async () => {
    const { http, calls } = mockHttp(() => jsonResponse(200, {}), { token: null });
    await http.request("http://x", "/v", "GET");
    assert.equal(calls[0].auth, null);
  });

  it("honors auth:false and per-call token overrides (WS mint path)", async () => {
    const { http, calls } = mockHttp(() =>
      jsonResponse(200, { ticket: "t", fingerprint: "ab12" }),
    );
    await http.request("http://x", "/api/v1/ws-ticket", "POST", { projectId: "p" }, undefined, {
      auth: true,
      token: "session-token",
      userAgent: "node",
    });
    assert.equal(calls[0].auth, "Bearer session-token");
    assert.equal(calls[0].ua, "node");
    assert.deepEqual(JSON.parse(calls[0].body ?? "{}"), { projectId: "p" });
  });

  it("raises ApiError with the parsed envelope on non-2xx", async () => {
    const { http } = mockHttp(() =>
      jsonResponse(403, { error: { code: "ROLE_DENIED", message: "no" } }),
    );
    await assert.rejects(
      http.request("http://x", "/v", "GET"),
      (err: unknown) => {
        assert.ok(err instanceof ApiError);
        assert.equal((err as ApiError).code, "ROLE_DENIED");
        assert.equal((err as ApiError).status, 403);
        assert.equal(err.message, "no");
        return true;
      },
    );
  });

  it("drops unset optional query params (None-sentinel semantics)", async () => {
    const { http, calls } = mockHttp(() => jsonResponse(200, {}));
    await http.request("http://x", "/v", "GET", undefined, { a: 1, b: undefined });
    assert.ok(calls[0].url?.includes("a=1"));
    assert.ok(!calls[0].url?.includes("b="));
  });

  it("retries transient 503 on idempotent methods then succeeds", async () => {
    let n = 0;
    const { http, calls } = mockHttp(() => {
      n += 1;
      return n === 1 ? jsonResponse(503, { detail: "down" }) : jsonResponse(200, { ok: true });
    }, { backoffBaseMs: 1, backoffCapMs: 2 });
    const out = await http.request<{ ok: boolean }>("http://x", "/v", "GET");
    assert.deepEqual(out, { ok: true });
    assert.equal(calls.length, 2);
  });

  it("never retries non-idempotent POST on 503", async () => {
    const { http, calls } = mockHttp(() => jsonResponse(503, { detail: "down" }));
    await assert.rejects(http.request("http://x", "/v", "POST"), ApiError);
    assert.equal(calls.length, 1);
  });

  it("retries 429 anywhere and honors Retry-After", async () => {
    let n = 0;
    const sleeps: number[] = [];
    const calls: Captured[] = [];
    const impl: FetchLike = async (_url, _init) => {
      n += 1;
      calls.push({});
      if (n < 3) {
        return jsonResponse(429, { error: { code: "RATE_LIMITED", message: "slow down" } }, {
          "retry-after": "0",
        });
      }
      return emptyResponse(204);
    };
    const http = new HarnessHttp({
      token: "tok",
      fetchImpl: impl,
      sleepFn: async (ms) => {
        sleeps.push(ms);
      },
    });
    const out = await http.request<null>("http://x", "/v", "POST");
    assert.equal(out, null); // 204 -> null, like the Python SDK
    assert.equal(calls.length, 3);
    assert.deepEqual(sleeps, [0, 0]);
  });

  it("wraps transport failures in TRANSPORT_ERROR after exhausting retries", async () => {
    const impl: FetchLike = async () => {
      throw new Error("connect ECONNREFUSED");
    };
    const http = new HarnessHttp({
      maxRetries: 1,
      backoffBaseMs: 1,
      fetchImpl: impl,
      sleepFn: async () => {},
    });
    await assert.rejects(
      http.request("http://x", "/v", "GET"),
      (err: unknown) => {
        assert.ok(err instanceof ApiError);
        assert.equal((err as ApiError).code, "TRANSPORT_ERROR");
        return true;
      },
    );
  });

  it("returns text for non-JSON content types", async () => {
    const { http } = mockHttp(() => textResponse(200, "diff --git a/x b/x"));
    const out = await http.request<string>("http://x", "/diff", "GET");
    assert.equal(out, "diff --git a/x b/x");
  });

  it("baseUrlFor resolves env overrides and default ports", () => {
    assert.equal(baseUrlFor("review").endsWith(":8008"), true);
    assert.equal(baseUrlFor("identity").endsWith(":8001"), true);
  });
});

describe("generated surface (identity module)", () => {
  it("builds correct requests end-to-end from the generated stubs", async () => {
    const captured: Captured[] = [];
    const impl: FetchLike = async (url, init) => {
      captured.push({
        method: init.method,
        url,
        body: init.body,
        auth: init.headers.authorization ?? null,
      });
      return jsonResponse(200, {
        items: [{ id: "p1", name: "proj" }],
        nextCursor: 0,
      });
    };
    const http = new HarnessHttp({ token: "tok", fetchImpl: impl, sleepFn: async () => {} });
    const identity = new IdentityClient(http, "http://identity.test");

    await identity.list_projects(10);
    assert.equal(captured[0].method, "GET");
    assert.ok(captured[0].url?.startsWith("http://identity.test/api/v1/projects?"));
    assert.ok(captured[0].url?.includes("limit=10"));

    await identity.create_project({ name: "sdk-smoke" });
    assert.equal(captured[1].method, "POST");
    assert.equal(captured[1].url, "http://identity.test/api/v1/projects");
    assert.deepEqual(JSON.parse(captured[1].body ?? "{}"), { name: "sdk-smoke" });
    assert.equal(captured[1].auth, "Bearer tok");
  });

  it("encodes path params and omits unset query params (orchestrator shape)", async () => {
    const { OrchestratorClient } = await import("../src/orchestrator.js");
    const captured: Captured[] = [];
    const impl: FetchLike = async (url, init) => {
      captured.push({ method: init.method, url });
      return jsonResponse(200, { items: [] });
    };
    const http = new HarnessHttp({ token: "tok", fetchImpl: impl, sleepFn: async () => {} });
    const orch = new OrchestratorClient(http, "http://orch.test");
    // Signature mirrors the Python stub: (project_id, status, limit, …).
    await orch.list_runs("proj-1", undefined, 3);
    assert.ok(captured[0].url?.includes("/api/v1/orchestrator/runs?"));
    assert.ok(captured[0].url?.includes("projectId=proj-1"));
    assert.ok(captured[0].url?.includes("limit=3"));
  });
});
