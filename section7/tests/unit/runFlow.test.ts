import { describe, expect, it } from "vitest";
import { composerMode, linesToList, nextStep, parseDraft } from "@/components/workspace/runFlow";

describe("composer rules", () => {
  it("picks the mode from the run state", () => {
    expect(composerMode(null)).toBe("goal");
    expect(composerMode("awaiting_plan_approval")).toBe("plan");
    expect(composerMode("executing")).toBe("direction");
    expect(composerMode("pr_opened")).toBe("finished");
  });
  it("treats plain text as text and only a leading slash as a command", () => {
    expect(parseDraft("  fix a/b  ", "executing")).toEqual({ kind: "text", text: "fix a/b" });
    expect(parseDraft("/pause", "executing")).toEqual({ kind: "command", name: "pause" });
    expect(parseDraft("/STOP now", "paused")).toEqual({ kind: "command", name: "stop" });
    expect(parseDraft("   ", "executing")).toEqual({ kind: "empty" });
  });
  it("refuses unknown commands and commands that do nothing in this state", () => {
    expect(parseDraft("/retry", "executing").kind).toBe("error");
    expect(parseDraft("/pause", "paused")).toMatchObject({ kind: "error", message: expect.stringContaining("not executing") });
    expect(parseDraft("/stop", null).kind).toBe("error");
  });
  it("asks one question when a run stops, and nothing while it works", () => {
    expect(nextStep("pr_opened")?.action).toBe("diff");
    expect(nextStep("paused")?.action).toBe("resume");
    expect(nextStep("stopped")?.action).toBe("new");
    expect(nextStep("executing")).toBeNull();
  });
  it("maps editor lines to list items", () => {
    expect(linesToList(" a \n\n b\r\nc ")).toEqual(["a", "b", "c"]);
  });
});
