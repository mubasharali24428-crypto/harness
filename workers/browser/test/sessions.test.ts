import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { executeAction } from "../src/actions.js";
import { SessionManager } from "../src/session.js";
import { WorkerError, type SessionIdentity } from "../src/types.js";
import {
  mintFor,
  newManager,
  startFixturePair,
  TEST_SECRET,
  testIdentity,
} from "./support/harness.js";

describe("session lifecycle and isolation", () => {
  let pair: Awaited<ReturnType<typeof startFixturePair>>;
  let mgr: Awaited<ReturnType<typeof newManager>>;
  let root = "";

  beforeAll(async () => {
    pair = await startFixturePair();
    mgr = await newManager();
    root = mgr.root;
  });

  afterAll(async () => {
    await mgr.manager.dispose();
    mgr.cleanup();
    await pair.close();
  });

  async function cookieText(session: Awaited<ReturnType<Awaited<ReturnType<typeof newManager>>["manager"]["open"]>>) {
    return session.page.evaluate(() => document.body.textContent ?? "");
  }

  it("persists cookies across close/reopen for the same identity", async () => {
    const identity: SessionIdentity = testIdentity({ runId: "run-persist" });
    const cap = () => mintFor(identity, [pair.siteA.origin]);

    const first = await mgr.manager.open(identity, { allowedOrigins: [pair.siteA.origin] });
    await executeAction(
      first,
      { kind: "navigate", url: `${pair.siteA.origin}/cookie/set?name=sid&value=abc`, capabilityToken: cap() },
      { capabilitySecret: TEST_SECRET },
    );
    await mgr.manager.close(first.sessionId);
    expect(existsSync(join(root, identity.teamId, identity.projectId, identity.runId, "state.json"))).toBe(true);

    const second = await mgr.manager.open(identity, { allowedOrigins: [pair.siteA.origin] });
    await executeAction(
      second,
      { kind: "navigate", url: `${pair.siteA.origin}/cookie/get`, capabilityToken: cap() },
      { capabilitySecret: TEST_SECRET },
    );
    expect(await cookieText(second)).toContain("sid=abc");
  });

  it("keeps different teams in totally separate contexts", async () => {
    const other = testIdentity({ teamId: "team-other", runId: "run-persist" });
    const session = await mgr.manager.open(other, { allowedOrigins: [pair.siteA.origin] });
    await executeAction(
      session,
      {
        kind: "navigate",
        url: `${pair.siteA.origin}/cookie/get`,
        capabilityToken: mintFor(other, [pair.siteA.origin]),
      },
      { capabilitySecret: TEST_SECRET },
    );
    expect(await cookieText(session)).not.toContain("sid=abc");
  });

  it("deleteState removes both the stored profile and the directory", async () => {
    const identity: SessionIdentity = testIdentity({ runId: "run-delete" });
    const cap = () => mintFor(identity, [pair.siteA.origin]);
    const dir = join(root, identity.teamId, identity.projectId, identity.runId);

    const first = await mgr.manager.open(identity, { allowedOrigins: [pair.siteA.origin] });
    await executeAction(
      first,
      { kind: "navigate", url: `${pair.siteA.origin}/cookie/set?name=tmp&value=1`, capabilityToken: cap() },
      { capabilitySecret: TEST_SECRET },
    );
    await mgr.manager.close(first.sessionId, { deleteState: true });
    expect(existsSync(dir)).toBe(false);

    const second = await mgr.manager.open(identity, { allowedOrigins: [pair.siteA.origin] });
    await executeAction(
      second,
      { kind: "navigate", url: `${pair.siteA.origin}/cookie/get`, capabilityToken: cap() },
      { capabilitySecret: TEST_SECRET },
    );
    expect(await cookieText(second)).not.toContain("tmp=1");
  });

  it("rejects path-traversal identities before touching the filesystem", async () => {
    try {
      await mgr.manager.open(testIdentity({ runId: "../evil" }), {
        allowedOrigins: [pair.siteA.origin],
      });
      expect.unreachable("open should reject");
    } catch (error) {
      expect(error).toBeInstanceOf(WorkerError);
      expect((error as WorkerError).code).toBe("BAD_REQUEST");
    }
  });

  it("rejects non-bare allowed origins", async () => {
    try {
      await mgr.manager.open(testIdentity({ runId: "run-badorigin" }), {
        allowedOrigins: [`${pair.siteA.origin}/`],
      });
      expect.unreachable("open should reject");
    } catch (error) {
      expect((error as WorkerError).code).toBe("BAD_REQUEST");
    }
  });
});

describe("sealed state and durable replay (A3 audit follow-up)", () => {
  const KEY = "ab".repeat(32);
  const tmpRoot = () => mkdtempSync(join(tmpdir(), "bw-seal-"));

  it("seal/unseal roundtrips and rejects tamper or a wrong key", async () => {
    const { seal, unseal, parseStateKey } = await import("../src/seal.js");
    const key = parseStateKey(KEY)!;
    const blob = seal('{"cookies":[]}', key);
    expect(blob).not.toContain("cookies");
    expect(unseal(blob, key)).toBe('{"cookies":[]}');
    expect(() => unseal(blob, parseStateKey("cd".repeat(32))!)).toThrow(/authentication/);
    const t = JSON.parse(blob);
    t.ct = Buffer.from("x".repeat(10)).toString("base64");
    expect(() => unseal(JSON.stringify(t), key)).toThrow(/authentication/);
    expect(() => parseStateKey("short")).toThrow(/64 hex/);
  });

  it("writes only state.enc with a key, resumes cookies, and refuses to open without the key", async () => {
    const root = tmpRoot();
    const id = { teamId: "t1", projectId: "p1", runId: "r1" };
    const opts = { allowedOrigins: ["http://127.0.0.1:9"] };
    const m1 = new SessionManager({ rootDir: root, stateKey: KEY });
    const s1 = await m1.open(id, opts);
    await s1.context.addCookies([{ name: "sid", value: "topsecret", url: "http://127.0.0.1:9" }]);
    await m1.close(s1.sessionId);
    await m1.dispose();
    const dir = join(root, "t1", "p1", "r1");
    expect(existsSync(join(dir, "state.enc"))).toBe(true);
    expect(existsSync(join(dir, "state.json"))).toBe(false);
    expect(readFileSync(join(dir, "state.enc"), "utf8")).not.toContain("topsecret");

    const m2 = new SessionManager({ rootDir: root, stateKey: KEY });
    const s2 = await m2.open(id, opts);
    expect((await s2.context.cookies()).some((c) => c.name === "sid" && c.value === "topsecret")).toBe(true);
    await m2.dispose();

    const m3 = new SessionManager({ rootDir: root });
    await expect(m3.open(id, opts)).rejects.toThrow(/encrypted/);
    await m3.dispose();
  });

  it("replay log survives a reopen", async () => {
    const root = tmpRoot();
    const id = { teamId: "t1", projectId: "p1", runId: "r2" };
    const m1 = new SessionManager({ rootDir: root });
    const s1 = await m1.open(id, { allowedOrigins: ["http://127.0.0.1:9"] });
    s1.recordReplay({ index: 0, action: { kind: "scroll" }, status: "succeeded", startedAtMs: 1, durationMs: 2, replayable: true });
    await m1.dispose();
    const m2 = new SessionManager({ rootDir: root });
    const s2 = await m2.open(id, { allowedOrigins: ["http://127.0.0.1:9"] });
    expect(s2.replayLog).toHaveLength(1);
    expect(s2.replayLog[0]?.action.kind).toBe("scroll");
    await m2.dispose();
  });
});
