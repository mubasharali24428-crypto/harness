import { describe, expect, it } from "vitest";
import { ApprovalStore } from "../src/approvals.js";
import { WorkerError } from "../src/types.js";
import { testIdentity } from "./support/harness.js";

function store(clock: { now: number }) {
  return new ApprovalStore(() => clock.now);
}

describe("ApprovalStore", () => {
  it("issues and consumes exactly once", () => {
    const clock = { now: 1_000_000 };
    const approvals = store(clock);
    const identity = testIdentity();
    const arguments_ = { kind: "click", target: { by: "selector", selector: "#send" }, url: "http://x/" };
    const grant = approvals.issue({ identity, operation: "click", arguments: arguments_, requestedBy: "alice" });
    expect(grant.expiresAtMs).toBe(1_000_000 + 120_000);
    const consumed = approvals.consume(grant.approvalId, { identity, operation: "click", arguments: arguments_ });
    expect(consumed.approvalId).toBe(grant.approvalId);
  });

  it("rejects replay with a distinct error", () => {
    const clock = { now: 1_000_000 };
    const approvals = store(clock);
    const identity = testIdentity();
    const args = { a: 1 };
    const grant = approvals.issue({ identity, operation: "click", arguments: args, requestedBy: "alice" });
    approvals.consume(grant.approvalId, { identity, operation: "click", arguments: args });
    try {
      approvals.consume(grant.approvalId, { identity, operation: "click", arguments: args });
      expect.unreachable();
    } catch (error) {
      expect((error as WorkerError).code).toBe("APPROVAL_REPLAYED");
    }
  });

  it("rejects expired grants", () => {
    const clock = { now: 1_000_000 };
    const approvals = store(clock);
    const identity = testIdentity();
    const args = { a: 1 };
    const grant = approvals.issue({
      identity,
      operation: "click",
      arguments: args,
      requestedBy: "alice",
      ttlSeconds: 30,
    });
    clock.now += 30_001;
    try {
      approvals.consume(grant.approvalId, { identity, operation: "click", arguments: args });
      expect.unreachable();
    } catch (error) {
      expect((error as WorkerError).code).toBe("APPROVAL_EXPIRED");
    }
  });

  it("binds to the exact argument digest", () => {
    const clock = { now: 1_000_000 };
    const approvals = store(clock);
    const identity = testIdentity();
    const grant = approvals.issue({
      identity,
      operation: "click",
      arguments: { kind: "click", target: "#a" },
      requestedBy: "alice",
    });
    try {
      approvals.consume(grant.approvalId, {
        identity,
        operation: "click",
        arguments: { kind: "click", target: "#b" },
      });
      expect.unreachable();
    } catch (error) {
      expect((error as WorkerError).code).toBe("APPROVAL_INVALID");
    }
  });

  it("binds to the issuing session identity and operation", () => {
    const clock = { now: 1_000_000 };
    const approvals = store(clock);
    const args = { a: 1 };
    const grant = approvals.issue({
      identity: testIdentity({ runId: "run-1" }),
      operation: "click",
      arguments: args,
      requestedBy: "alice",
    });
    try {
      approvals.consume(grant.approvalId, {
        identity: testIdentity({ runId: "run-2" }),
        operation: "click",
        arguments: args,
      });
      expect.unreachable();
    } catch (error) {
      expect((error as WorkerError).code).toBe("APPROVAL_INVALID");
    }
    try {
      approvals.consume(grant.approvalId, {
        identity: testIdentity({ runId: "run-1" }),
        operation: "type",
        arguments: args,
      });
      expect.unreachable();
    } catch (error) {
      expect((error as WorkerError).code).toBe("APPROVAL_INVALID");
    }
  });
});
