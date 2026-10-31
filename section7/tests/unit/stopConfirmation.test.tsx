// W23 slice 4: "stopped" only when the server confirms the engine is gone.
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { RunStatus } from "@contracts/api";
import { Controls, stopNotice } from "../../src/components/Controls";
import { runSettled } from "../../app/runs/runsApi";

describe("stopNotice", () => {
  it("claims a confirmed stop only for `stopped`", () => {
    expect(stopNotice("stopped")).toMatch(/^Stopped\. Confirmed/);
    expect(stopNotice("completed")).toMatch(/already finished/);
    for (const s of ["stopping", "", "paused", undefined]) {
      expect(stopNotice(s), String(s)).toMatch(/not confirmed yet: the agent may still change files/);
    }
  });
});

describe("Controls", () => {
  it("shows no stop notice before a stop", () => {
    const html = renderToStaticMarkup(
      <Controls runId="r1" token="t" status={{ state: "executing" } as RunStatus}
        onDiffChange={() => {}} onActionComplete={() => {}} />,
    );
    expect(html).not.toContain("stop-notice");
  });
});

describe("runSettled", () => {
  const lc = (mutationPossible: boolean) => ({ phase: "x", cancelConfirmed: !mutationPossible, mutationPossible });

  it("keeps polling a done run whose engine can still change files", () => {
    expect(runSettled({ state: "done", lifecycle: lc(true) })).toBe(false);
    expect(runSettled({ state: "done", lifecycle: lc(false) })).toBe(true);
  });

  it("settles on state alone for an older server, and never while the run is live", () => {
    expect(runSettled({ state: "done", lifecycle: undefined })).toBe(true);
    expect(runSettled({ state: "done", lifecycle: null })).toBe(true);
    expect(runSettled({ state: "executing", lifecycle: lc(false) })).toBe(false);
    expect(runSettled(null)).toBe(false);
  });
});
