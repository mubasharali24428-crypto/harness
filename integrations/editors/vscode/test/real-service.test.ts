// The TS client against the REAL developer-workflow service (python, fake identity/sandbox
// upstream only): proves the wire contract — camelCase keys, bearer auth, decisions — end to end.
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";
import { existsSync } from "node:fs";
import { HarnessClient } from "../src/core/protocol";

const svc = path.resolve(__dirname, "../../service");
const py = path.join(svc, ".venv/bin/python");
let proc: ChildProcess;
let info: { base: string; changeSetId: string; sessionId: string };

describe.skipIf(!existsSync(py))("client <-> real service", () => {
  beforeAll(async () => {
    proc = spawn(py, [path.join(svc, "tests/serve_e2e.py")], { stdio: ["ignore", "pipe", "inherit"] });
    info = await new Promise((resolve, reject) => {
      let buf = "";
      proc.stdout!.on("data", (d) => {
        buf += d;
        const line = buf.split("\n").find((l) => l.startsWith("{"));
        if (line) resolve(JSON.parse(line));
      });
      proc.on("exit", () => reject(new Error("service exited")));
    });
  });
  afterAll(() => proc?.kill("SIGKILL"));

  const client = (token: string) => new HarnessClient({ workflow: info.base }, async () => token);

  it("lists sessions/change sets and records a hunk decision", async () => {
    const c = client("tok-eng-a");
    const sessions = await c.codeSessions("proj-a");
    expect(sessions.map((s) => s.sessionId)).toContain(info.sessionId);
    const sets = await c.changeSets(info.sessionId);
    expect(sets[0].changeSetId).toBe(info.changeSetId);
    expect(sets[0].files[0].hunks).toHaveLength(2);
    const after = await c.decide(info.changeSetId, [{ path: "src/app.py", hunkId: "h0", decision: "accept" }]);
    expect(after.files[0].decisions.h0.decision).toBe("accept");
  });
  it("a read-only role is denied and a bad token is rejected, with clear messages", async () => {
    await expect(client("tok-view-a").decide(info.changeSetId, [{ path: "src/app.py", hunkId: "h1", decision: "accept" }]))
      .rejects.toMatchObject({ status: 403, code: "ROLE_DENIED" });
    await expect(client("nope").changeSets(info.sessionId)).rejects.toMatchObject({ status: 401 });
  });
  it("another team cannot see the session", async () => {
    await expect(client("tok-eng-b").changeSets(info.sessionId)).rejects.toMatchObject({ status: 404 });
  });
});
