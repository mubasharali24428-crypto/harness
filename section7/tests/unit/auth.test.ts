import { describe, expect, it } from "vitest";
import type { Principal } from "@contracts/roles";
import {
  clearSession,
  getGoal,
  getPrId,
  getProjectId,
  getRunId,
  getToken,
  getUserId,
  loadSession,
  saveSession,
  setGoal,
  setPrId,
  setProjectId,
  setRunId,
  setUserId,
} from "@/lib/auth";

const principal: Principal = { userId: "u-1", teamId: "t-1", role: "admin" };

// The auth helpers no-op outside a browser (typeof window === "undefined" guard),
// and S7's vitest runs in the node environment — which is itself a regression
// guard: these functions must never throw when localStorage is absent (SSR/tests).
describe("session helpers (node env — guards must no-op, not throw)", () => {
  it("loadSession returns null without a window", () => {
    expect(loadSession()).toBeNull();
    expect(getToken()).toBeNull();
  });

  it("save/clear and typed setters are safe no-ops without a window", () => {
    expect(() => saveSession("tok", principal)).not.toThrow();
    expect(() => clearSession()).not.toThrow();
    expect(() => setProjectId("p")).not.toThrow();
    expect(() => setRunId("r")).not.toThrow();
    expect(() => setPrId("7")).not.toThrow();
    expect(() => setGoal("g")).not.toThrow();
    expect(() => setUserId("u")).not.toThrow();
    expect(getProjectId()).toBeNull();
    expect(getUserId()).toBeNull();
  });
});
