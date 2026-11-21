// M19-N4 — demoEntry pure helpers (route-module law: the page itself may
// only default-export, so the shared constants live here and get tested
// directly — same pattern as prSource/prEmptyState).
import { describe, expect, it } from "vitest";
import { DEMO_LANDING_QUERY, demoLandingHref } from "./demoEntry";

describe("demo entry landing href", () => {
  it("lands on the seeded run's detail page", () => {
    expect(
      demoLandingHref({ runId: "demo-run-1", projectId: "demo-proj-1" }),
    ).toBe("/projects/demo-proj-1/runs/demo-run-1?tab=changes&demo=1");
  });

  it("URL-encodes path segments (ids are server-minted, never trusted)", () => {
    expect(demoLandingHref({ runId: "a/b", projectId: "c d" })).toBe(
      "/projects/c%20d/runs/a%2Fb?tab=changes&demo=1",
    );
  });

  it("carries the demo=1 honesty marker query", () => {
    expect(DEMO_LANDING_QUERY).toBe("tab=changes&demo=1");
    expect(demoLandingHref({ runId: "r", projectId: "p" }).endsWith("&demo=1")).toBe(
      true,
    );
  });
});
