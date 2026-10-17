import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { clickApprovalArguments, executeAction } from "../src/actions.js";
import type { SessionIdentity, TargetSpec } from "../src/types.js";
import {
  mintFor,
  newManager,
  startFixturePair,
  TEST_SECRET,
  testIdentity,
} from "./support/harness.js";

describe("egress, external writes, approvals and capability scope", () => {
  let pair: Awaited<ReturnType<typeof startFixturePair>>;
  let mgr: Awaited<ReturnType<typeof newManager>>;
  const identity: SessionIdentity = testIdentity({ runId: "run-ext" });

  beforeAll(async () => {
    pair = await startFixturePair();
    mgr = await newManager();
  });

  afterAll(async () => {
    await mgr.manager.dispose();
    mgr.cleanup();
    await pair.close();
  });

  async function openWith(origins: string[], runId = "run-ext") {
    const id = testIdentity({ runId });
    const session = await mgr.manager.open(id, { allowedOrigins: origins });
    const act = (action: Parameters<typeof executeAction>[1]) =>
      executeAction(session, action, { capabilitySecret: TEST_SECRET });
    return { id, session, act, cap: () => mintFor(id, origins) };
  }

  it("requires an approval grant for a flagged external write, then executes it once approved", async () => {
    const { id, session, act, cap } = await openWith([pair.siteA.origin, pair.siteB.origin]);
    await act({
      kind: "navigate",
      url: `${pair.siteA.origin}/external-form`,
      capabilityToken: cap(),
    });

    const target: TargetSpec = { by: "selector", selector: "#send-report" };
    const unapproved = await act({
      kind: "click",
      target,
      effect: "external_write",
      capabilityToken: cap(),
    });
    expect(unapproved.status).toBe("needsApproval");
    expect(unapproved.error?.code).toBe("APPROVAL_REQUIRED");
    const details = (unapproved.result as { details: { operation: string; argumentsDigest: string } })
      .details;
    expect(details.operation).toBe("click");
    expect(typeof details.argumentsDigest).toBe("string");

    const grant = session.approvals.issue({
      identity: id,
      operation: "click",
      arguments: clickApprovalArguments(target, session.page.url()),
      requestedBy: "tester",
    });

    const approved = await act({
      kind: "click",
      target,
      effect: "external_write",
      approvalId: grant.approvalId,
      capabilityToken: cap(),
      expect: { urlContains: `${pair.siteB.origin}/collect`, selectorVisible: "#collected" },
    });
    expect(approved.status).toBe("succeeded");

    const replayed = await act({
      kind: "click",
      target,
      effect: "external_write",
      approvalId: grant.approvalId,
      capabilityToken: cap(),
    });
    expect(replayed.status).toBe("denied");
    expect(replayed.error?.code).toBe("APPROVAL_REPLAYED");
  });

  it("auto-detects an external link and demands approval even without an explicit flag", async () => {
    const { act, cap } = await openWith([pair.siteA.origin], "run-ext2");
    await act({ kind: "navigate", url: `${pair.siteA.origin}/external-link`, capabilityToken: cap() });
    const outcome = await act({
      kind: "click",
      target: { by: "role", role: "link", name: "Visit external" },
      capabilityToken: cap(),
    });
    expect(outcome.status).toBe("needsApproval");
    expect(outcome.error?.code).toBe("APPROVAL_REQUIRED");
  });

  it("blocks a redirect that lands on a disallowed origin and resets the page", async () => {
    const { session, act, cap } = await openWith([pair.siteA.origin], "run-ext3");
    const outcome = await act({
      kind: "navigate",
      url: `${pair.siteA.origin}/redirect`,
      capabilityToken: cap(),
    });
    expect(outcome.status).toBe("denied");
    expect(outcome.error?.code).toBe("SSRF_DENIED");
    expect(session.page.url()).toBe("about:blank");
  });

  it("denies navigation to a loopback origin outside the allowlist", async () => {
    const { act, cap } = await openWith([pair.siteA.origin], "run-ext4");
    const outcome = await act({
      kind: "navigate",
      url: `${pair.siteB.origin}/landing`,
      capabilityToken: cap(),
    });
    expect(outcome.status).toBe("denied");
    expect(outcome.error?.code).toBe("SSRF_DENIED");
  });

  it("denies a capability token minted for a different run", async () => {
    const { session, act } = await openWith([pair.siteA.origin], "run-ext5");
    const foreign = mintFor(testIdentity({ runId: "run-elsewhere" }), [pair.siteA.origin]);
    const outcome = await act({
      kind: "navigate",
      url: `${pair.siteA.origin}/form`,
      capabilityToken: foreign,
    });
    expect(outcome.status).toBe("denied");
    expect(outcome.error?.code).toBe("CAPABILITY_SCOPE");
    expect(session.page.url()).not.toContain("/form");
  });

  it("denies expired tokens and tokens signed with the wrong secret", async () => {
    const { act, cap } = await openWith([pair.siteA.origin], "run-ext6");
    const past = Math.floor(Date.now() / 1000) - 3600;
    const expired = mintFor(testIdentity({ runId: "run-ext6" }), [pair.siteA.origin], {
      nowSec: past,
      ttlSeconds: 10,
    });
    const expiredOutcome = await act({
      kind: "navigate",
      url: `${pair.siteA.origin}/form`,
      capabilityToken: expired,
    });
    expect(expiredOutcome.error?.code).toBe("CAPABILITY_EXPIRED");

    const forged = mintFor(testIdentity({ runId: "run-ext6" }), [pair.siteA.origin], {
      secret: "not-the-secret",
    });
    const forgedOutcome = await act({
      kind: "navigate",
      url: `${pair.siteA.origin}/form`,
      capabilityToken: forged,
    });
    expect(forgedOutcome.error?.code).toBe("CAPABILITY_INVALID");

    const ok = await act({ kind: "navigate", url: `${pair.siteA.origin}/form`, capabilityToken: cap() });
    expect(ok.status).toBe("succeeded");
  });
});
