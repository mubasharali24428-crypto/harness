import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  mintCapability,
  SessionManager,
  type CapabilityPayload,
  type SessionIdentity,
} from "../../src/index.js";

export const TEST_SECRET = "a3-browser-test-secret";

export function testIdentity(overrides: Partial<SessionIdentity> = {}): SessionIdentity {
  return {
    teamId: "team-a3",
    projectId: "proj-a3",
    runId: "run-1",
    ...overrides,
  };
}

export function mintFor(
  identity: SessionIdentity,
  allowedOrigins: string[],
  opts: { ttlSeconds?: number; nowSec?: number; secret?: string; taskId?: string } = {},
): string {
  const nowSec = opts.nowSec ?? Math.floor(Date.now() / 1000);
  const payload: CapabilityPayload = {
    v: 1,
    op: "browser",
    taskId: opts.taskId ?? "task-a3",
    teamId: identity.teamId,
    projectId: identity.projectId,
    runId: identity.runId,
    allowedOrigins,
    iat: nowSec,
    exp: nowSec + (opts.ttlSeconds ?? 300),
    nonce: `n-${nowSec}`,
  };
  return mintCapability(opts.secret ?? TEST_SECRET, payload);
}

export function tempRoot(): { root: string; cleanup: () => void } {
  const root = mkdtempSync(join(tmpdir(), "a3-browser-test-"));
  return { root, cleanup: () => rmSync(root, { recursive: true, force: true }) };
}

export async function startFixturePair() {
  const { startSite } = await import("../fixtures/site.mjs");
  const siteB = await startSite();
  const siteA = await startSite({ externalBase: siteB.origin });
  return {
    siteA,
    siteB,
    close: async () => {
      await siteA.close();
      await siteB.close();
    },
  };
}

export async function newManager(): Promise<{ manager: SessionManager; root: string; cleanup: () => void }> {
  const { root, cleanup } = tempRoot();
  const manager = new SessionManager({ rootDir: root, headless: true });
  return { manager, root, cleanup };
}

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
