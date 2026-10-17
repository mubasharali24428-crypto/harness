import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { executeAction } from "../src/actions.js";
import type { SessionIdentity } from "../src/types.js";
import {
  mintFor,
  newManager,
  sleep,
  startFixturePair,
  TEST_SECRET,
  testIdentity,
} from "./support/harness.js";

describe("target resolution", () => {
  let pair: Awaited<ReturnType<typeof startFixturePair>>;
  let mgr: Awaited<ReturnType<typeof newManager>>;
  let session: Awaited<ReturnType<Awaited<ReturnType<typeof newManager>>["manager"]["open"]>>;
  const identity: SessionIdentity = testIdentity({ runId: "run-targets" });

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

  it("refuses ambiguous role+name targets with candidate details", async () => {
    await act({ kind: "navigate", url: `${pair.siteA.origin}/form`, capabilityToken: cap() });
    const outcome = await act({
      kind: "click",
      target: { by: "role", role: "button", name: "Submit" },
      capabilityToken: cap(),
    });
    expect(outcome.status).toBe("failed");
    expect(outcome.error?.code).toBe("AMBIGUOUS_TARGET");
    expect(JSON.stringify(outcome.result)).toContain("candidates");
  });

  it("resolves a unique selector and clicks it", async () => {
    const outcome = await act({
      kind: "click",
      target: { by: "selector", selector: "#dup1" },
      capabilityToken: cap(),
      expect: { urlContains: "/submitted" },
    });
    expect(outcome.status).toBe("succeeded");
  });

  it("resolves a unique role+name and follows a JS navigation", async () => {
    await act({ kind: "navigate", url: `${pair.siteA.origin}/form`, capabilityToken: cap() });
    const outcome = await act({
      kind: "click",
      target: { by: "role", role: "button", name: "Send local" },
      capabilityToken: cap(),
      expect: { urlContains: "/submitted" },
    });
    expect(outcome.status).toBe("succeeded");
  });

  it("reports TARGET_NOT_FOUND for selectors that match nothing", async () => {
    const outcome = await act({
      kind: "click",
      target: { by: "selector", selector: "#does-not-exist" },
      capabilityToken: cap(),
    });
    expect(outcome.error?.code).toBe("TARGET_NOT_FOUND");
  });

  it("resolves refs from the named observation", async () => {
    await act({ kind: "navigate", url: `${pair.siteA.origin}/form`, capabilityToken: cap() });
    const obs = await act({ kind: "observe", capabilityToken: cap() });
    const ref = obs.observation!.refs.find((r) => r.name === "Send local")!;
    const outcome = await act({
      kind: "click",
      target: { by: "ref", ref: ref.ref },
      observationId: obs.observation!.observationId,
      capabilityToken: cap(),
      expect: { urlContains: "/submitted" },
    });
    expect(outcome.status).toBe("succeeded");
  });

  it("rejects refs that name an unknown observation", async () => {
    const outcome = await act({
      kind: "click",
      target: { by: "ref", ref: "e1" },
      observationId: "obs_does-not-exist",
      capabilityToken: cap(),
    });
    expect(outcome.error?.code).toBe("BAD_REQUEST");
  });

  it("rejects refs whose element moved beyond tolerance (STALE_OBSERVATION)", async () => {
    await act({ kind: "navigate", url: `${pair.siteA.origin}/moving`, capabilityToken: cap() });
    const obs = await act({ kind: "observe", capabilityToken: cap() });
    const ref = obs.observation!.refs.find((r) => r.name === "Move me")!;
    expect(ref).toBeDefined();
    await sleep(250);
    const outcome = await act({
      kind: "click",
      target: { by: "ref", ref: ref.ref },
      observationId: obs.observation!.observationId,
      capabilityToken: cap(),
    });
    expect(outcome.status).toBe("failed");
    expect(outcome.error?.code).toBe("STALE_OBSERVATION");
  });
});
