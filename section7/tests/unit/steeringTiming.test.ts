// W23: the run page says when guidance reaches the agent, for every server value.
import { describe, expect, it } from "vitest";
import { takesEffectText } from "../../app/projects/[projectId]/runs/[runId]/collabModel";

describe("takesEffectText", () => {
  it("says when, for each value collab.takes_effect sends", () => {
    expect(takesEffectText("next_start")).toMatch(/next starts or resumes/);
    expect(takesEffectText("next_execution")).toMatch(/current pass will not see this/);
    expect(takesEffectText("never")).toMatch(/will not see this/);
  });

  it("never claims a time for a queued, unknown or missing value", () => {
    // after_acceptance has its own queued notice in the panel
    for (const v of ["after_acceptance", "", "soon", "constructor", undefined, null, 3]) {
      expect(takesEffectText(v), String(v)).toBeNull();
    }
  });
});
