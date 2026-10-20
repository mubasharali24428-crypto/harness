// Parts 12.010 / 24.010: a run's state is shown in plain language, for every state the orchestrator can report.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { stateLabel } from "../../app/runs/runsApi";

const machine = readFileSync(resolve(process.cwd(), "../section4/src/harness_orchestrator/state_machine.py"), "utf8");
const agentStates = [...machine.split("class AgentState(StrEnum):")[1].split("\n\n")[0].matchAll(/= "([a-z_]+)"/g)].map((m) => m[1]);

describe("stateLabel", () => {
  it("has a plain sentence for every AgentState the orchestrator defines", () => {
    expect(agentStates.length).toBe(10);
    for (const s of agentStates) {
      const label = stateLabel(s);
      expect(label, s).not.toMatch(/_/);
      expect(label, s).not.toBe(s);
      expect(label[0], s).toBe(label[0].toUpperCase());
    }
  });

  it("says who has to act when the run is waiting", () => {
    expect(stateLabel("awaiting_plan_approval")).toContain("approve");
    expect(stateLabel("blocked")).toContain("person");
  });

  it("names how a run ended, and humanizes anything unknown instead of dropping it", () => {
    expect(stateLabel("stopped")).toBe("Stopped by a person");
    expect(stateLabel("cancelled")).toBe("Cancelled");
    expect(stateLabel("rolled_back")).toBe("Rolled back");
    expect(stateLabel("")).toBe("Unknown");
    expect(stateLabel("constructor")).toBe("Constructor"); // not an own key of the table
  });
});
