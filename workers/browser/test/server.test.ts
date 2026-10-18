import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createComputerUseServer, section1Auth } from "../src/server.js";
import { PreviewRegistry } from "../src/preview.js";
import { mintFor, newManager, startFixturePair, TEST_SECRET } from "./support/harness.js";

// Faithful local stub of the Section 1 endpoints this adapter consumes.
const PRINCIPALS: Record<string, { userId: string; teamId: string; role: string }> = {
  engA: { userId: "u1", teamId: "team-a", role: "engineer" },
  adminA: { userId: "u2", teamId: "team-a", role: "admin" },
  viewA: { userId: "u3", teamId: "team-a", role: "viewer" },
  engB: { userId: "u4", teamId: "team-b", role: "engineer" },
};
const PROJECTS: Record<string, string[]> = { "team-a": ["proj-a"], "team-b": ["proj-b"] };

function listen(server: Server): Promise<string> {
  return new Promise((r) => server.listen(0, "127.0.0.1", () => r(`http://127.0.0.1:${(server.address() as AddressInfo).port}`)));
}

describe("computer-use HTTP adapter", () => {
  let s1: Server, api: Server, base: string;
  let pair: Awaited<ReturnType<typeof startFixturePair>>;
  let mgr: Awaited<ReturnType<typeof newManager>>;

  beforeAll(async () => {
    s1 = createServer((req, res) => {
      const p = PRINCIPALS[(req.headers.authorization ?? "").replace("Bearer ", "")];
      if (!p) { res.writeHead(401); return void res.end("{}"); }
      res.setHeader("content-type", "application/json");
      if (req.url === "/api/v1/me") return void res.end(JSON.stringify(p));
      if (req.url === "/api/v1/projects") {
        return void res.end(JSON.stringify({ items: (PROJECTS[p.teamId] ?? []).map((id) => ({ id, name: id })) }));
      }
      res.writeHead(404); res.end("{}");
    });
    const s1Base = await listen(s1);
    pair = await startFixturePair();
    mgr = await newManager();
    api = createComputerUseServer({ manager: mgr.manager, capabilitySecret: TEST_SECRET, previews: new PreviewRegistry({ minPort: 1024, maxPort: 65535, reservedPorts: [] }), ...section1Auth(s1Base) });
    base = await listen(api);
  });
  afterAll(async () => {
    await mgr.manager.dispose(); mgr.cleanup(); await pair.close();
    s1.close(); api.close();
  });

  const call = async (token: string | null, method: string, path: string, body?: unknown) => {
    const res = await fetch(`${base}/api/v1/computer-use${path}`, {
      method,
      headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    return { status: res.status, json: (await res.json().catch(() => null)) as any };
  };
  const open = async (token: string, extra: object = {}) => {
    const port = Number(new URL(pair.siteA.origin).port);
    await call(token, "POST", "/previews", { port });
    return call(token, "POST", "/sessions", { projectId: "proj-a", runId: "run-http", allowedOrigins: [pair.siteA.origin], ...extra });
  };

  it("rejects missing/invalid bearer and read-only roles", async () => {
    expect((await call(null, "POST", "/sessions", {})).status).toBe(401);
    expect((await call("nope", "POST", "/sessions", {})).status).toBe(401);
    expect((await open("viewA")).status).toBe(403);
  });

  it("ignores a body teamId and refuses a project the team does not own", async () => {
    // team-a registers the shared fixture port first; a port belongs to one team on shared loopback.
    const ok = await open("engA", { teamId: "team-b" });
    expect(ok.status).toBe(201);
    expect((await open("engB", { teamId: "team-a" })).status).toBe(404);
    const session = mgr.manager.get(ok.json.sessionId);
    expect(session.identity.teamId).toBe("team-a");
  });

  it("runs actions for the owning team only, and returns the error envelope", async () => {
    const { json } = await open("engA", { runId: "run-act" });
    const id = json.sessionId as string;
    const cap = mintFor({ teamId: "team-a", projectId: "proj-a", runId: "run-act" }, [pair.siteA.origin]);
    const nav = await call("engA", "POST", `/sessions/${id}/actions`, {
      schemaVersion: "hap/1", requestId: "r1", projectId: "proj-a", runId: "run-act",
      operation: "navigate", arguments: { url: `${pair.siteA.origin}/form`, capabilityToken: cap },
    });
    expect(nav.json.status).toBe("succeeded");
    // Cross-team access is indistinguishable from "does not exist".
    const other = await call("engB", "POST", `/sessions/${id}/actions`, { operation: "observe", arguments: {} });
    expect(other.status).toBe(404);
    expect(other.json.error.code).toBe("session_not_found");
    // Missing capability => denied, not executed.
    const nocap = await call("engA", "POST", `/sessions/${id}/actions`, {
      projectId: "proj-a", runId: "run-act", operation: "observe", arguments: {},
    });
    expect(nocap.json.status).toBe("denied");
    const unsupported = await call("engA", "POST", `/sessions/${id}/actions`, { operation: "rm-rf", arguments: {} });
    expect(unsupported.json.status).toBe("unsupported");
  });

  it("only admins grant approvals", async () => {
    const { json } = await open("engA", { runId: "run-apr" });
    const body = { target: { by: "selector", selector: "#x" } };
    expect((await call("engA", "POST", `/sessions/${json.sessionId}/approvals`, body)).status).toBe(403);
    const granted = await call("adminA", "POST", `/sessions/${json.sessionId}/approvals`, body);
    expect(granted.status).toBe(201);
    expect(granted.json.audience).toBe("browser-worker");
  });
});
