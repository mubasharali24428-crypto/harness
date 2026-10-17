import { existsSync } from "node:fs";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { executeAction } from "../src/actions.js";
import { sha256Hex } from "../src/digest.js";
import type { SessionIdentity } from "../src/types.js";
import {
  mintFor,
  newManager,
  startFixturePair,
  TEST_SECRET,
  testIdentity,
} from "./support/harness.js";

describe("action execution", () => {
  let pair: Awaited<ReturnType<typeof startFixturePair>>;
  let mgr: Awaited<ReturnType<typeof newManager>>;
  let session: Awaited<ReturnType<Awaited<ReturnType<typeof newManager>>["manager"]["open"]>>;
  const identity: SessionIdentity = testIdentity({ runId: "run-acts" });

  beforeAll(async () => {
    pair = await startFixturePair();
    mgr = await newManager();
    session = await mgr.manager.open(identity, { allowedOrigins: [pair.siteA.origin] });
  });

  afterAll(async () => {
    await mgr.manager.dispose();
    mgr.cleanup();
    await pair.close();
  });

  const act = (action: Parameters<typeof executeAction>[1]) =>
    executeAction(session, action, { capabilitySecret: TEST_SECRET });
  const cap = () => mintFor(identity, [pair.siteA.origin]);
  const go = (path: string) => act({ kind: "navigate", url: `${pair.siteA.origin}${path}`, capabilityToken: cap() });

  it("types into an input and proves the value without echoing it", async () => {
    await go("/form");
    const outcome = await act({
      kind: "type",
      target: { by: "selector", selector: "#note" },
      text: "hunter2",
      capabilityToken: cap(),
      expect: { valueEquals: { target: { by: "selector", selector: "#note" }, equals: "hunter2" } },
    });
    expect(outcome.status).toBe("succeeded");
    expect(outcome.result?.valueDigest).toBe(sha256Hex("hunter2"));
    expect(JSON.stringify(outcome.result)).not.toContain("hunter2");
  });

  it("fails the postcondition when the expected selector never appears", async () => {
    await go("/form");
    const outcome = await act({
      kind: "click",
      target: { by: "role", role: "button", name: "Send local" },
      capabilityToken: cap(),
      expect: { selectorVisible: "#definitely-not-here" },
    });
    expect(outcome.status).toBe("failed");
    expect(outcome.error?.code).toBe("POSTCONDITION_FAILED");
  });

  it("selects an option and verifies it", async () => {
    await go("/form");
    const outcome = await act({
      kind: "selectOption",
      target: { by: "selector", selector: "#color" },
      value: "blue",
      capabilityToken: cap(),
      expect: { valueEquals: { target: { by: "selector", selector: "#color" }, equals: "blue" } },
    });
    expect(outcome.status).toBe("succeeded");
  });

  it("presses Enter inside a form and lands on the form action", async () => {
    await go("/form");
    const outcome = await act({
      kind: "press",
      key: "Enter",
      target: { by: "selector", selector: "#note" },
      capabilityToken: cap(),
      expect: { urlContains: "/submitted" },
    });
    expect(outcome.status).toBe("succeeded");
  });

  it("scrolls by delta and to a target, reporting positions", async () => {
    await go("/tall");
    const byDelta = await act({ kind: "scroll", dy: 600, capabilityToken: cap() });
    expect(byDelta.status).toBe("succeeded");
    expect((byDelta.result?.scroll as { y: number }).y).toBeGreaterThanOrEqual(500);

    const toTarget = await act({
      kind: "scroll",
      toTarget: { by: "selector", selector: "#bottom" },
      capabilityToken: cap(),
    });
    expect(toTarget.status).toBe("succeeded");
    expect((toTarget.result?.scroll as { y: number }).y).toBeGreaterThan(1000);
  });

  it("waits for a delayed element and for a URL", async () => {
    await go("/delayed");
    const ok = await act({
      kind: "waitFor",
      target: { by: "selector", selector: "#later" },
      timeoutMs: 3000,
      capabilityToken: cap(),
    });
    expect(ok.status).toBe("succeeded");

    const fail = await act({
      kind: "waitFor",
      urlContains: "never-arrives",
      timeoutMs: 200,
      capabilityToken: cap(),
    });
    expect(fail.status).toBe("failed");
    expect(fail.error?.code).toBe("POSTCONDITION_FAILED");
  });

  it("captures full-page screenshots as artifacts on disk", async () => {
    const outcome = await act({ kind: "screenshot", fullPage: true, capabilityToken: cap() });
    expect(outcome.status).toBe("succeeded");
    const artifact = outcome.artifacts[0]!;
    expect(artifact.path && existsSync(artifact.path)).toBe(true);
    expect(artifact.sizeBytes).toBeGreaterThan(0);
  });

  describe("coordinate-space clicks", () => {
    it("clicks a ref at its center and lands inside the canvas", async () => {
      await go("/canvas");
      const obs = await act({ kind: "observe", capabilityToken: cap() });
      const canvasRef = obs.observation!.refs.find((r) => r.tag === "canvas");
      expect(canvasRef).toBeDefined();
      const outcome = await act({
        kind: "click",
        target: { by: "ref", ref: canvasRef!.ref },
        observationId: obs.observation!.observationId,
        capabilityToken: cap(),
      });
      expect(outcome.status).toBe("succeeded");
      const clicks = (await session.page.evaluate(() => (window as unknown as { __clicks?: Array<{ x: number; y: number }> }).__clicks)) ?? [];
      expect(clicks.length).toBe(1);
      expect(Math.abs(clicks[0]!.x - 150)).toBeLessThanOrEqual(2);
      expect(Math.abs(clicks[0]!.y - 100)).toBeLessThanOrEqual(2);
    });

    it("maps image-space points through DPR 1 geometry", async () => {
      const obs = await act({ kind: "observe", capabilityToken: cap() });
      const canvasRef = obs.observation!.refs.find((r) => r.tag === "canvas")!;
      const outcome = await act({
        kind: "click",
        target: {
          by: "point",
          x: canvasRef.box.x + 20,
          y: canvasRef.box.y + 15,
          space: "image",
        },
        observationId: obs.observation!.observationId,
        capabilityToken: cap(),
      });
      expect(outcome.status).toBe("succeeded");
      const clicks = (await session.page.evaluate(() => (window as unknown as { __clicks?: Array<{ x: number; y: number }> }).__clicks)) ?? [];
      const last = clicks[clicks.length - 1]!;
      expect(Math.abs(last.x - 20)).toBeLessThanOrEqual(2);
      expect(Math.abs(last.y - 15)).toBeLessThanOrEqual(2);
    });

    it("maps image-space points through DPR 2 geometry", async () => {
      const identity2 = testIdentity({ runId: "run-dpr2" });
      const cap2 = () => mintFor(identity2, [pair.siteA.origin]);
      const session2 = await mgr.manager.open(identity2, {
        allowedOrigins: [pair.siteA.origin],
        deviceScaleFactor: 2,
      });
      await executeAction(
        session2,
        { kind: "navigate", url: `${pair.siteA.origin}/canvas`, capabilityToken: cap2() },
        { capabilitySecret: TEST_SECRET },
      );
      const obsOutcome = await executeAction(
        session2,
        { kind: "observe", capabilityToken: cap2() },
        { capabilitySecret: TEST_SECRET },
      );
      const obs = obsOutcome.observation!;
      expect(obs.viewport.deviceScaleFactor).toBe(2);
      const canvasRef = obs.refs.find((r) => r.tag === "canvas")!;
      const outcome = await executeAction(
        session2,
        {
          kind: "click",
          target: {
            by: "point",
            x: (canvasRef.box.x + 20) * 2,
            y: (canvasRef.box.y + 15) * 2,
            space: "image",
          },
          observationId: obs.observationId,
          capabilityToken: cap2(),
        },
        { capabilitySecret: TEST_SECRET },
      );
      expect(outcome.status).toBe("succeeded");
      const clicks =
        (await session2.page.evaluate(
          () => (window as unknown as { __clicks?: Array<{ x: number; y: number }> }).__clicks,
        )) ?? [];
      const last = clicks[clicks.length - 1]!;
      expect(Math.abs(last.x - 20)).toBeLessThanOrEqual(2);
      expect(Math.abs(last.y - 15)).toBeLessThanOrEqual(2);
      await mgr.manager.close(session2.sessionId, { deleteState: true });
    });
  });
});
