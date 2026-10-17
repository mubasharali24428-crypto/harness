import { beforeEach, describe, expect, it, vi } from "vitest";
import * as mock from "./vscode-mock";
import { activate } from "../src/extension";

const TOKEN = "tok-SECRET-12345678";
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function ctx() {
  const secrets = new Map<string, string>();
  const global = new Map<string, unknown>();
  return {
    subscriptions: [] as unknown[],
    secrets: { get: async (k: string) => secrets.get(k), store: async (k: string, v: string) => void secrets.set(k, v), delete: async (k: string) => void secrets.delete(k) },
    globalState: { get: (k: string) => global.get(k), update: async (k: string, v: unknown) => void (v === undefined ? global.delete(k) : global.set(k, v)) },
    _secrets: secrets, _global: global,
  };
}
const run = (id: string) => mock.state.commands.get(id)!();

let calls: { url: string; method: string; body: unknown; auth?: string }[];
let routes: Record<string, (b: unknown) => Response>;

beforeEach(() => {
  mock.reset();
  calls = [];
  routes = {};
  mock.state.config = { identityUrl: "http://localhost:1", workflowUrl: "http://localhost:2", orchestratorUrl: "http://localhost:3" };
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ url, method: init.method ?? "GET", body, auth: (init.headers as Record<string, string>).authorization });
    const key = `${init.method} ${new URL(url).pathname}`;
    return (routes[key] ?? (() => json({ error: { code: "NOT_FOUND", message: "nf" } }, 404)))(body);
  });
});

describe("activation", () => {
  it("registers every contributed command", () => {
    activate(ctx() as never);
    expect([...mock.state.commands.keys()].sort()).toEqual([
      "harness.approvePlan", "harness.reviewChangeSets", "harness.runStatus",
      "harness.selectProject", "harness.signIn", "harness.signOut"]);
  });
});

describe("auth storage", () => {
  it("verifies the token, then stores it in SecretStorage only", async () => {
    const c = ctx(); activate(c as never);
    routes["GET /api/v1/me"] = () => json({ userId: "u1", teamId: "t1", role: "engineer" });
    mock.state.inputs.push(TOKEN);
    await run("harness.signIn");
    expect(c._secrets.get("harness.sessionToken")).toBe(TOKEN);
    expect(JSON.stringify([...c._global])).not.toContain(TOKEN);
    expect(mock.state.infos[0]).toMatch(/signed in as u1 \(engineer\) on team t1/);
    expect(JSON.stringify(mock.state)).not.toContain(TOKEN);
  });
  it("a rejected token is never persisted", async () => {
    const c = ctx(); activate(c as never);
    routes["GET /api/v1/me"] = () => json({ error: { code: "UNAUTHORIZED", message: "bad" } }, 401);
    mock.state.inputs.push(TOKEN);
    await run("harness.signIn");
    expect(c._secrets.size).toBe(0);
    expect(mock.state.errors[0]).toMatch(/Sign in again/);
  });
  it("sign out clears the secret and the selected project", async () => {
    const c = ctx(); activate(c as never);
    c._secrets.set("harness.sessionToken", TOKEN); c._global.set("harness.projectId", "p1");
    await run("harness.signOut");
    expect(c._secrets.size).toBe(0); expect(c._global.size).toBe(0);
  });
  it("errors are logged without the token", async () => {
    const c = ctx(); activate(c as never);
    c._secrets.set("harness.sessionToken", TOKEN);
    routes["GET /api/v1/orchestrator/runs/r1/status"] = () => json({ error: { code: "X", message: `leaked ${TOKEN}` } }, 500);
    mock.state.inputs.push("r1");
    await run("harness.runStatus");
    expect(mock.state.output.join("\n")).not.toContain(TOKEN);
    expect(mock.state.errors.join("\n")).not.toContain(TOKEN);
  });
});

describe("project selection and unsupported states", () => {
  it("selects a project; review without one says so", async () => {
    const c = ctx(); activate(c as never);
    c._secrets.set("harness.sessionToken", TOKEN);
    await run("harness.reviewChangeSets");
    expect(mock.state.errors[0]).toMatch(/Select a project first/);
    routes["GET /api/v1/projects"] = () => json({ items: [{ id: "p1", name: "Demo" }] });
    mock.state.picks.push((items: { id: string }[]) => items[0]);
    await run("harness.selectProject");
    expect(c._global.get("harness.projectId")).toBe("p1");
  });
  it("an unset service is reported as unsupported, not faked", async () => {
    const c = ctx(); activate(c as never);
    c._secrets.set("harness.sessionToken", TOKEN);
    mock.state.config.orchestratorUrl = "";
    mock.state.inputs.push("r1");
    await run("harness.runStatus");
    expect(mock.state.errors[0]).toMatch(/not configured/);
  });
  it("refuses insecure remote http configuration", async () => {
    const c = ctx(); activate(c as never);
    c._secrets.set("harness.sessionToken", TOKEN);
    mock.state.config.identityUrl = "http://evil.example.com";
    await run("harness.selectProject");
    expect(mock.state.errors[0]).toMatch(/https/);
    expect(calls).toHaveLength(0);
  });
});

describe("approval and review workflows", () => {
  it("plan approval needs an explicit modal confirmation", async () => {
    const c = ctx(); activate(c as never);
    c._secrets.set("harness.sessionToken", TOKEN);
    routes["POST /api/v1/orchestrator/runs/r1/plan/approve"] = () => json({ status: "approved" });
    mock.state.inputs.push("r1", "p1"); mock.state.warnAnswer = undefined; // user dismisses
    await run("harness.approvePlan");
    expect(calls).toHaveLength(0);
    expect((mock.state.warnings[0].opts as { modal: boolean }).modal).toBe(true);
    mock.state.inputs.push("r1", "p1"); mock.state.warnAnswer = "Approve";
    await run("harness.approvePlan");
    expect(calls).toEqual([expect.objectContaining({ method: "POST", body: { planId: "p1" }, auth: `Bearer ${TOKEN}` })]);
  });
  it("a viewer's approval is denied by the server and surfaced, not hidden", async () => {
    const c = ctx(); activate(c as never);
    c._secrets.set("harness.sessionToken", TOKEN);
    routes["POST /api/v1/orchestrator/runs/r1/plan/approve"] = () => json({ error: { code: "ROLE_DENIED", message: "no" } }, 403);
    mock.state.inputs.push("r1", "p1"); mock.state.warnAnswer = "Approve";
    await run("harness.approvePlan");
    expect(mock.state.errors[0]).toMatch(/role/);
    expect(mock.state.infos).not.toContain("Harness: plan approved");
  });
  it("hunk review sends decisions per hunk and Stop halts", async () => {
    const c = ctx(); activate(c as never);
    c._secrets.set("harness.sessionToken", TOKEN); c._global.set("harness.projectId", "p1");
    const hunk = (id: string) => ({ id, lines: [{ kind: "add", text: id }] });
    routes["GET /api/v1/developer-workflow/code-sessions"] = () => json({ items: [{ sessionId: "s1", branch: "main" }] });
    routes["GET /api/v1/developer-workflow/code-sessions/s1/change-sets"] = () => json({ items: [
      { changeSetId: "cs1", title: "T", status: "open", files: [{ path: "a.py", hunks: [hunk("h0"), hunk("h1")], decisions: {} }] }] });
    routes["POST /api/v1/developer-workflow/change-sets/cs1/decisions"] = () => json({});
    mock.state.picks.push((i: { id: string }[]) => i[0], (i: unknown[]) => i[0], "Accept", "Stop");
    await run("harness.reviewChangeSets");
    const posts = calls.filter((x) => x.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({ decisions: [{ path: "a.py", hunkId: "h0", decision: "accept" }] });
  });
});
