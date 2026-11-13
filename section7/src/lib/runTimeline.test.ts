// U2 timeline slice — pure rules under test with a synthetic 40-step run.
// No browser: grouping, state-at-step, scrub keys and follow are plain functions.
import { describe, expect, it } from "vitest";
import {
  groupEvents,
  reduceFollow,
  scrubIndex,
  shortCommand,
  stateAtStep,
  type TimelineEventInput,
} from "./runTimeline";

const TS = "2026-11-13T12:00:00Z";

function milestone(n: number): TimelineEventInput {
  if (n === 1) return { seq: n, type: "plan_proposed", ts: TS, payload: { steps: ["one", "two"] } };
  if (n === 20) return { seq: n, type: "execution_started", ts: TS, payload: {} };
  if (n === 40) return { seq: n, type: "pr_opened", ts: TS, payload: { url: "https://example.test/pull/1" } };
  return { seq: n, type: "terminal_output", ts: TS, payload: {} };
}

const FORTY = Array.from({ length: 40 }, (_, i) => milestone(i + 1));

describe("groupEvents — 40-step synthetic run", () => {
  it("makes one block per step and keeps oldest-first sequence order", () => {
    const steps = groupEvents(FORTY);
    expect(steps).toHaveLength(40);
    expect(steps[0].seq).toBe(1);
    expect(steps[19].seq).toBe(20);
    expect(steps[39].seq).toBe(40);
    expect(steps[0].label).toBe("Plan proposed");
    expect(steps[0].receipt).toBe("2 proposed steps");
  });

  it("pairs a tool_call with its tool_result into one block", () => {
    const steps = groupEvents([
      {
        seq: 1,
        type: "tool_call",
        ts: TS,
        payload: { callId: "c1", name: "read_file", arguments: { path: "src/app.ts" } },
      },
      {
        seq: 2,
        type: "tool_result",
        ts: TS,
        payload: { callId: "c1", name: "read_file", ok: true, durationMs: 42 },
      },
    ]);
    expect(steps).toHaveLength(1);
    expect(steps[0].id).toBe("call:c1");
    expect(steps[0].status).toBe("ok");
    expect(steps[0].receipt).toBe("ok · 42 ms");
    expect(steps[0].endSeq).toBe(2);
  });

  it("marks a failed result and never renders the output body", () => {
    const steps = groupEvents([
      { seq: 1, type: "tool_call", ts: TS, payload: { callId: "c2", name: "run", arguments: { command: "npm test" } } },
      {
        seq: 2,
        type: "tool_result",
        ts: TS,
        payload: { callId: "c2", name: "run", ok: false, error: "exit 1\nstack…", output: "SECRET LOG" },
      },
    ]);
    expect(steps[0].status).toBe("failed");
    expect(steps[0].receipt).toContain("failed: exit 1");
    expect(JSON.stringify(steps)).not.toContain("SECRET LOG");
    expect(JSON.stringify(steps)).not.toContain("stack");
  });

  it("drops env assignments from a command receipt", () => {
    const steps = groupEvents([
      {
        seq: 1,
        type: "tool_call",
        ts: TS,
        payload: { callId: "c3", name: "run", arguments: { command: "API_KEY=supersecret npm test" } },
      },
    ]);
    expect(steps[0].receipt).toBe("$ npm test");
    expect(JSON.stringify(steps)).not.toContain("supersecret");
    expect(shortCommand("TOKEN=abc\nnpm test")).toBe("npm test");
  });

  it("keeps an orphan tool_result as its own block instead of dropping it", () => {
    const steps = groupEvents([{ seq: 7, type: "tool_result", ts: TS, payload: { callId: "lost", ok: true } }]);
    expect(steps).toHaveLength(1);
    expect(steps[0].id).toBe("seq:7");
    expect(steps[0].status).toBe("ok");
  });
});

describe("stateAtStep — scrub to step 1, 20 and 40", () => {
  const steps = groupEvents(FORTY);

  it("reads the state recorded by the selected step", () => {
    expect(stateAtStep(steps, 0).state).toBe("awaiting_plan_approval");
    expect(stateAtStep(steps, 19).state).toBe("executing");
    expect(stateAtStep(steps, 39).state).toBe("pr_opened");
  });

  it("carries the nearest earlier state forward and names its source", () => {
    const middle = stateAtStep(steps, 5);
    expect(middle.state).toBe("awaiting_plan_approval");
    expect(middle.seq).toBe(1);
    expect(middle.label).toBe("Plan proposed");
  });

  it("is honest when no event records a state", () => {
    const noState = groupEvents([{ seq: 1, type: "terminal_output", ts: TS, payload: {} }]);
    expect(stateAtStep(noState, 0)).toEqual({ state: null, seq: null, label: null });
    expect(stateAtStep([], 0)).toEqual({ state: null, seq: null, label: null });
  });

  it("clamps an out-of-range selection instead of crashing", () => {
    expect(stateAtStep(steps, -5).state).toBe("awaiting_plan_approval");
    expect(stateAtStep(steps, 999).state).toBe("pr_opened");
  });
});

describe("scrubIndex — j/k and Home/End", () => {
  it("moves from step 1 to 20 to 40 and back", () => {
    let i = scrubIndex(-1, 40, "Home");
    expect(i).toBe(0);
    for (let n = 0; n < 19; n += 1) i = scrubIndex(i, 40, "j");
    expect(i).toBe(19); // step 20
    i = scrubIndex(i, 40, "End");
    expect(i).toBe(39); // step 40
    i = scrubIndex(i, 40, "k");
    expect(i).toBe(38);
    expect(scrubIndex(i, 40, "ArrowUp")).toBe(37);
    expect(scrubIndex(i, 40, "ArrowDown")).toBe(39);
  });

  it("clamps at both ends and handles an empty run", () => {
    expect(scrubIndex(0, 40, "k")).toBe(0);
    expect(scrubIndex(39, 40, "j")).toBe(39);
    expect(scrubIndex(0, 0, "j")).toBe(-1);
    expect(scrubIndex(3, 40, "x")).toBe(3);
  });
});

describe("reduceFollow — follow on/off", () => {
  it("turns off on a manual move and back on when the switch is pressed", () => {
    let follow = true;
    follow = reduceFollow(follow, "manual-move");
    expect(follow).toBe(false);
    follow = reduceFollow(follow, "toggle");
    expect(follow).toBe(true);
  });

  it("stays on when new steps arrive and off after a manual move", () => {
    expect(reduceFollow(true, "new-step")).toBe(true);
    expect(reduceFollow(false, "new-step")).toBe(false);
    expect(reduceFollow(false, "manual-move")).toBe(false);
    expect(reduceFollow(true, "toggle")).toBe(false);
  });
});

describe("receipts never show secret shapes (2026-11-13 review)", () => {
  it("masks bearer tokens, URL credentials, --token= and key-shaped error text", async () => {
    const { shortCommand, toolResultReceipt, toolCallReceipt } = await import("@/lib/runTimeline");
    const gh = ["ghp", "_", "b".repeat(36)].join("");
    const sk = ["sk", "-proj-", "SECRETabc12345"].join("");
    const shown = [
      shortCommand('curl -H "Authorization: Bearer abc123SECRET" https://x'),
      shortCommand(`git push https://user:${gh}@github.com/o/r`),
      shortCommand("deploy --token=xyzSECRET"),
      toolResultReceipt({ ok: false, error: `401: invalid key ${sk}` }) ?? "",
      toolCallReceipt({ name: "run", arguments: { command: `curl -u me:${gh} https://api` } }) ?? "",
    ].join(" | ");
    for (const secret of ["abc123SECRET", gh, "xyzSECRET", sk]) expect(shown).not.toContain(secret);
  });
});
