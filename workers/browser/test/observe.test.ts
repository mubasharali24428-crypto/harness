import { existsSync, readFileSync } from "node:fs";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { executeAction } from "../src/actions.js";
import { sha256Hex } from "../src/digest.js";
import type { ActionOutcome, SessionIdentity } from "../src/types.js";
import {
  mintFor,
  newManager,
  startFixturePair,
  TEST_SECRET,
  testIdentity,
} from "./support/harness.js";

describe("observation capture", () => {
  let pair: Awaited<ReturnType<typeof startFixturePair>>;
  let mgr: Awaited<ReturnType<typeof newManager>>;
  let session: Awaited<ReturnType<Awaited<ReturnType<typeof newManager>>["manager"]["open"]>>;
  const identity: SessionIdentity = testIdentity({ runId: "run-obs" });

  beforeAll(async () => {
    pair = await startFixturePair();
    mgr = await newManager();
    session = await mgr.manager.open(identity, { allowedOrigins: [pair.siteA.origin, pair.siteB.origin] });
  });

  afterAll(async () => {
    await mgr.manager.dispose();
    mgr.cleanup();
    await pair.close();
  });

  const act = (action: Parameters<typeof executeAction>[1]) =>
    executeAction(session, action, { capabilitySecret: TEST_SECRET });

  const cap = () => mintFor(identity, [pair.siteA.origin]);

  it("navigates, observes interactive elements with refs, and writes a verifiable screenshot artifact", async () => {
    const nav = await act({ kind: "navigate", url: `${pair.siteA.origin}/form`, capabilityToken: cap() });
    expect(nav.status).toBe("succeeded");

    const outcome = await act({ kind: "observe", screenshot: true, capabilityToken: cap() });
    expect(outcome.status).toBe("succeeded");
    expect(outcome.observation).toBeDefined();
    const obs = outcome.observation!;
    expect(obs.url).toContain("/form");
    expect(outcome.result?.refCount).toBe(obs.refs.length);

    const names = obs.refs.map((r) => r.name);
    expect(names).toContain("Send local");
    expect(names).toContain("note");
    expect(obs.refs.every((r) => /^f\d+:e\d+$/.test(r.ref))).toBe(true);

    const artifact = outcome.artifacts[0]!;
    expect(artifact.mediaType).toBe("image/png");
    expect(artifact.redactionStatus).toBe("none");
    expect(artifact.path).toBeDefined();
    expect(existsSync(artifact.path!)).toBe(true);
    expect(sha256Hex(readFileSync(artifact.path!))).toBe(artifact.sha256);
    expect(artifact.sizeBytes).toBeGreaterThan(0);
    expect(obs.screenshotArtifact?.sha256).toBe(artifact.sha256);
  });

  it("changes domDigest when the set of interactive elements changes", async () => {
    const first = await act({ kind: "observe", capabilityToken: cap() });
    await session.page.evaluate(() => {
      const b = document.createElement("button");
      b.textContent = "Injected action";
      document.body.appendChild(b);
    });
    const second = await act({ kind: "observe", capabilityToken: cap() });
    expect(second.observation!.domDigest).not.toBe(first.observation!.domDigest);
    expect(second.observation!.refs.some((r) => r.name === "Injected action")).toBe(true);
  });

  it("enumerates cross-origin frames and can still ref their elements", async () => {
    await act({ kind: "navigate", url: `${pair.siteA.origin}/crossorigin`, capabilityToken: cap() });
    const outcome = await act({ kind: "observe", capabilityToken: cap() });
    const obs = outcome.observation!;
    expect(obs.frames.length).toBe(2);
    expect(obs.frames[1]!.url).toContain(pair.siteB.origin);
    expect(obs.frames[1]!.crossOrigin).toBe(true);
    expect(obs.frames[0]!.crossOrigin).toBe(false);
    const frameRef = obs.refs.find((r) => r.frameIndex === 1 && r.name === "Frame button");
    expect(frameRef).toBeDefined();
    expect(frameRef!.ref.startsWith("f1:")).toBe(true);
  });

  it("reports viewport geometry including device scale factor", async () => {
    const outcome: ActionOutcome = await act({ kind: "observe", capabilityToken: cap() });
    const viewport = outcome.observation!.viewport;
    expect(viewport.width).toBeGreaterThan(0);
    expect(viewport.height).toBeGreaterThan(0);
    expect(viewport.deviceScaleFactor).toBe(1);
    expect(viewport.visualScale).toBe(1);
  });
});
