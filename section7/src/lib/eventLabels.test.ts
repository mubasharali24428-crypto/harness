import { describe, expect, it } from "vitest";

import { eventLabel, eventTone } from "./eventLabels";

describe("timeline labels", () => {
  it("uses the curated wording for known events", () => {
    expect(eventLabel("plan.proposed")).toBe("Plan proposed");
    expect(eventLabel("engine.failed")).toBe("Engine failed");
    expect(eventLabel("pr.opened")).toBe("Pull request opened");
  });

  it("degrades unknown types to a readable sentence case", () => {
    expect(eventLabel("subtask.blocker_added")).toBe("Subtask blocker added");
    expect(eventLabel("")).toBe("Event");
  });

  it("tones failures red and completions green", () => {
    expect(eventTone("engine.failed")).toBe("danger");
    expect(eventTone("run.abandoned")).toBe("danger");
    expect(eventTone("review.passed")).toBe("success");
    expect(eventTone("plan.approved")).toBe("success");
    expect(eventTone("execution.started")).toBe("accent");
  });
});
