// M14-F4 (Wave 4 lane F4) — optimistic engine tests, headless per repo
// convention (node-env vitest, no jsdom/RTL in this repo and installs are out
// of scope). The success / failure / rollback / apply-failure lifecycle is
// exercised through the framework-free runOptimistic engine; the hook shell
// gets an SSR smoke (initial status observable via renderToStaticMarkup —
// post-render transitions belong to the engine, which is fully covered here).
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  optimisticErrorMessage,
  runOptimistic,
  type OptimisticStatus,
} from "@/hooks/useOptimistic";
import { useOptimistic } from "@/hooks/useOptimistic";
import { ApiError } from "@/lib/api";

afterEach(() => {
  vi.restoreAllMocks();
});

function recorder() {
  const statuses: OptimisticStatus[] = [];
  return { statuses, onStatus: (s: OptimisticStatus) => statuses.push(s) };
}

describe("runOptimistic — success path", () => {
  it("apply → request → commit in order, reconciles with server truth", async () => {
    const trace: string[] = [];
    const rec = recorder();
    let local: string[] = ["before"];
    const outcome = await runOptimistic<string[], { role: string }>({
      apply: () => {
        trace.push("apply");
        const snapshot = local;
        local = ["optimistic-viewer"];
        return snapshot;
      },
      request: async () => {
        trace.push("request");
        return { role: "viewer-from-server" };
      },
      commit: (result) => {
        trace.push("commit");
        // Reconciliation uses SERVER truth, not the local guess.
        local = [`confirmed-${result.role}`];
      },
      rollback: () => trace.push("rollback-MUST-NOT-FIRE"),
      onStatus: rec.onStatus,
    });
    expect(outcome).toBe("confirmed");
    expect(trace).toEqual(["apply", "request", "commit"]);
    expect(local).toEqual(["confirmed-viewer-from-server"]);
    expect(rec.statuses).toEqual(["pending", "confirmed"]);
  });

  it("commit-less mutations stay confirmed without reconciliation", async () => {
    const rolledBack = vi.fn();
    const outcome = await runOptimistic({
      apply: () => ({ kept: true }),
      request: async () => "ok",
      rollback: rolledBack,
    });
    expect(outcome).toBe("confirmed");
    expect(rolledBack).not.toHaveBeenCalled();
  });
});

describe("runOptimistic — failure rolls back", () => {
  it("request failure restores the pre-apply snapshot and surfaces the error", async () => {
    const rec = recorder();
    let members = [{ id: "u2", role: "engineer" }];
    const errors: string[] = [];
    const original = members;
    const outcome = await runOptimistic<Array<{ id: string; role: string }>, null>({
      apply: () => {
        const snapshot = members;
        members = []; // row vanishes instantly
        return snapshot;
      },
      request: () =>
        Promise.reject(new ApiError("cannot remove the last admin", "LAST_ADMIN", 409)),
      rollback: (snapshot) => {
        members = snapshot; // row restored
      },
      onError: (m) => errors.push(m),
      onStatus: rec.onStatus,
    });
    expect(outcome).toBe("rolled-back");
    expect(members).toEqual(original); // prior row restored exactly
    expect(errors).toEqual(["cannot remove the last admin"]); // server text verbatim
    expect(rec.statuses).toEqual(["pending", "rolled-back"]);
  });

  it("LAST_ADMIN surfaces the SERVER message even when mapped codes exist nearby", async () => {
    // friendlyErrorMessage has no LAST_ADMIN key — the raw server sentence
    // must pass through untouched (W2-C1: never a fabricated excuse).
    const errors: string[] = [];
    await runOptimistic({
      apply: () => "snap",
      request: () =>
        Promise.reject(
          new ApiError("cannot demote the last admin", "LAST_ADMIN", 409),
        ),
      rollback: () => {},
      onError: (m) => errors.push(m),
    });
    expect(errors).toEqual(["cannot demote the last admin"]);
  });

  it("known codes get their M12-E2 friendly sentence instead of raw jargon", async () => {
    const errors: string[] = [];
    await runOptimistic({
      apply: () => "snap",
      request: () =>
        Promise.reject(new ApiError("PLAN_NOT_APPROVED", "PLAN_NOT_APPROVED", 400)),
      rollback: () => {},
      onError: (m) => errors.push(m),
    });
    expect(errors).toEqual([
      "This work can't start until someone approves the plan. Ask your team to review it first.",
    ]);
  });

  it("non-Error rejections still roll back and surface something honest", async () => {
    const errors: string[] = [];
    const rolledBack = vi.fn();
    const outcome = await runOptimistic({
      apply: () => "snap",
      request: () => Promise.reject("network died"),
      rollback: rolledBack,
      onError: (m) => errors.push(m),
    });
    expect(outcome).toBe("rolled-back");
    expect(rolledBack).toHaveBeenCalledWith("snap");
    expect(errors).toEqual(["network died"]);
  });
});

describe("runOptimistic — apply failure aborts cleanly", () => {
  it("never fires the request nor the rollback when apply throws", async () => {
    const request = vi.fn(async () => "unused");
    const rollback = vi.fn();
    const errors: string[] = [];
    const rec = recorder();
    const outcome = await runOptimistic({
      apply: () => {
        throw new Error("No active plan to act on.");
      },
      request,
      rollback,
      onError: (m) => errors.push(m),
      onStatus: rec.onStatus,
    });
    expect(outcome).toBe("apply-failed");
    expect(request).not.toHaveBeenCalled();
    expect(rollback).not.toHaveBeenCalled();
    expect(errors).toEqual(["No active plan to act on."]);
    expect(rec.statuses).toEqual(["failed"]);
  });
});

describe("runOptimistic — commit failures never un-confirm the server", () => {
  it("stays confirmed, surfaces the reconcile hiccup, skips rollback", async () => {
    const rollback = vi.fn();
    const errors: string[] = [];
    const rec = recorder();
    const outcome = await runOptimistic({
      apply: () => "snap",
      request: async () => "server-won",
      commit: () => {
        throw new Error("refresh failed");
      },
      rollback,
      onError: (m) => errors.push(m),
      onStatus: rec.onStatus,
    });
    expect(outcome).toBe("confirmed");
    expect(rollback).not.toHaveBeenCalled();
    expect(errors).toEqual(["refresh failed"]);
    expect(rec.statuses).toEqual(["pending", "confirmed"]);
  });
});

describe("optimisticErrorMessage — copy mapping", () => {
  it("friendly map wins for known codes", () => {
    expect(
      optimisticErrorMessage(new ApiError("x", "ROLE_DENIED", 403)),
    ).toContain("Your role doesn't allow that action.");
  });

  it("unknown codes keep the server's message verbatim", () => {
    expect(
      optimisticErrorMessage(new ApiError("cannot remove the last admin", "LAST_ADMIN", 409)),
    ).toBe("cannot remove the last admin");
  });

  it("empty-message ApiError falls back to a status-bearing line", () => {
    expect(optimisticErrorMessage(new ApiError("", "WEIRD", 503))).toBe(
      "Request failed (503)",
    );
  });

  it("plain Errors pass their message through; primitives stringify", () => {
    expect(optimisticErrorMessage(new Error("boom"))).toBe("boom");
    expect(optimisticErrorMessage(42)).toBe("42");
  });
});

describe("useOptimistic hook shell — SSR smoke", () => {
  it("initial status is idle and mutate/reset are stable functions", () => {
    let observed: {
      status: OptimisticStatus;
      pending: boolean;
      fnTypes: string;
    } | null = null;
    function Probe() {
      const { status, pending, mutate, reset } = useOptimistic<string, unknown>();
      observed = { status, pending, fnTypes: `${typeof mutate}:${typeof reset}` };
      return null;
    }
    renderToStaticMarkup(React.createElement(Probe));
    expect(observed).toEqual({ status: "idle", pending: false, fnTypes: "function:function" });
  });
});
