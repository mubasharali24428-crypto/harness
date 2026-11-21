// R1 preview rules — grouping, titles, run picking, money, and the unified-diff
// parser (src/components/workspace/runRows.ts, diffParse.ts + format.formatMoneyUsd).
// Node environment: pure functions only. E0 2026-11-21: previewData.ts was a
// misnamed module of pure helpers; its contents moved verbatim to runRows.ts /
// diffParse.ts and the module was deleted (see no-preview-data-in-routes.test.ts).
import { describe, expect, it } from "vitest";
import {
  groupForRun,
  groupRuns,
  pickRunId,
  runRowTitle,
} from "../../src/components/workspace/runRows";
import { fileStatLabel, parseUnifiedDiff } from "../../src/components/workspace/diffParse";
import { formatMoneyUsd } from "../../src/lib/format";
import type { RunDto } from "../../app/runs/runsApi";

function run(partial: Partial<RunDto> & { id: string }): RunDto {
  return {
    projectId: "proj-1",
    status: "done",
    createdAt: "2026-11-14T05:00:00Z",
    startedAt: "2026-11-14T05:01:00Z",
    finishedAt: null,
    ...partial,
  };
}

describe("groupRuns", () => {
  it("files each real AgentState into needsYou / working / done", () => {
    expect(groupForRun({ status: "awaiting_plan_approval" })).toBe("needsYou");
    expect(groupForRun({ status: "blocked" })).toBe("needsYou");
    expect(groupForRun({ status: "pr_opened" })).toBe("needsYou");
    expect(groupForRun({ status: "executing" })).toBe("working");
    expect(groupForRun({ status: "planning" })).toBe("working");
    expect(groupForRun({ status: "paused" })).toBe("working");
    expect(groupForRun({ status: "reviewing" })).toBe("working");
    expect(groupForRun({ status: "done" })).toBe("done");
    expect(groupForRun({ status: "idle" })).toBe("done");
    expect(groupForRun({ status: "failed" })).toBe("done");
  });

  it("orders needs-you oldest first and the rest newest first", () => {
    const grouped = groupRuns([
      run({ id: "r1", status: "pr_opened", startedAt: "2026-11-14T05:00:00Z" }),
      run({ id: "r2", status: "pr_opened", startedAt: "2026-11-14T06:00:00Z" }),
      run({ id: "r3", status: "executing", startedAt: "2026-11-14T04:00:00Z" }),
      run({ id: "r4", status: "executing", startedAt: "2026-11-14T07:00:00Z" }),
      run({ id: "r5", status: "done", startedAt: "2026-11-14T03:00:00Z" }),
    ]);
    expect(grouped.needsYou.map((r) => r.id)).toEqual(["r1", "r2"]);
    expect(grouped.working.map((r) => r.id)).toEqual(["r4", "r3"]);
    expect(grouped.done.map((r) => r.id)).toEqual(["r5"]);
  });

  it("lands an unknown state in working, never in done", () => {
    expect(groupForRun({ status: "mystery_state" })).toBe("working");
  });
});

describe("runRowTitle", () => {
  it("uses the plan goal, first line only", () => {
    expect(runRowTitle("Implement median\nsecond line")).toBe("Implement median");
  });
  it("cuts long goals at 68 chars", () => {
    const long = "x".repeat(90);
    expect(runRowTitle(long)).toHaveLength(68);
    expect(runRowTitle(long).endsWith("…")).toBe(true);
  });
  it("never shows a raw id as a title when there is no goal", () => {
    expect(runRowTitle("")).toBe("Untitled run");
    expect(runRowTitle(null)).toBe("Untitled run");
  });
});

describe("pickRunId", () => {
  const runs = [
    run({ id: "old", startedAt: "2026-11-13T05:00:00Z" }),
    run({ id: "new", startedAt: "2026-11-14T09:00:00Z" }),
  ];
  it("prefers the deep link", () => {
    expect(pickRunId(runs, "old", "new")).toBe("old");
  });
  it("falls back to the remembered run", () => {
    expect(pickRunId(runs, null, "new")).toBe("new");
  });
  it("falls back to the newest run", () => {
    expect(pickRunId(runs, null, null)).toBe("new");
  });
  it("returns null for an empty team (honest empty state)", () => {
    expect(pickRunId([], null, null)).toBeNull();
  });
  it("keeps a deep link that matches nothing so the route renders 404", () => {
    expect(pickRunId(runs, "ghost", "new")).toBe("ghost");
  });
});

describe("formatMoneyUsd — the R1 money rule", () => {
  it("two decimals from one cent up", () => {
    expect(formatMoneyUsd(1.5)).toBe("$1.50");
    expect(formatMoneyUsd(0.01)).toBe("$0.01");
    expect(formatMoneyUsd(12.5)).toBe("$12.50");
  });
  it("below a cent reads '< $0.01', never $0.000057", () => {
    expect(formatMoneyUsd(0.000057)).toBe("< $0.01");
    expect(formatMoneyUsd(0.009)).toBe("< $0.01");
  });
  it("a recorded zero is real data: $0.00", () => {
    expect(formatMoneyUsd(0)).toBe("$0.00");
  });
  it("unrecorded is 'unknown', never $0", () => {
    expect(formatMoneyUsd(null)).toBe("unknown");
    expect(formatMoneyUsd(undefined)).toBe("unknown");
    expect(formatMoneyUsd(Number.NaN)).toBe("unknown");
    expect(formatMoneyUsd(-1)).toBe("unknown");
  });
});

describe("parseUnifiedDiff", () => {
  const diff = [
    "diff --git a/src/textstats/stats.py b/src/textstats/stats.py",
    "index 7c4a3f2..a1b2c3d 100644",
    "--- a/src/textstats/stats.py",
    "+++ b/src/textstats/stats.py",
    "@@ -24,4 +24,9 @@ def median_word_length(text: str) -> float:",
    "-    raise NotImplementedError(\"demo task: implement median_word_length\")",
    "+    lengths = [len(word) for word in text.split()]",
    "+    if not lengths:",
    "+        return 0.0",
    " ",
  ].join("\n");

  it("splits files on the +++ header and strips the b/ prefix", () => {
    const files = parseUnifiedDiff(diff);
    expect(files).toHaveLength(1);
    expect(files[0].path).toBe("src/textstats/stats.py");
  });

  it("classifies add/del/context/meta lines with line numbers", () => {
    const lines = parseUnifiedDiff(diff)[0].lines;
    const meta = lines.find((l) => l.tone === "meta");
    expect(meta?.text.startsWith("@@")).toBe(true);
    const del = lines.filter((l) => l.tone === "del");
    expect(del).toHaveLength(1);
    expect(del[0].oldNo).toBe(24);
    const adds = lines.filter((l) => l.tone === "add");
    expect(adds).toHaveLength(3);
    expect(adds[0].newNo).toBe(24);
    expect(adds[2].newNo).toBe(26);
    const ctx = lines.filter((l) => l.tone === "context");
    expect(ctx).toHaveLength(1);
    expect(ctx[0].oldNo).toBe(25);
  });

  it("never throws on garbage input", () => {
    const files = parseUnifiedDiff("\u0000random\ntext\n");
    expect(files.length).toBeGreaterThan(0);
  });
});

describe("fileStatLabel", () => {
  it("renders +N −N and stays honest at zero", () => {
    expect(fileStatLabel({ path: "a", additions: 7, deletions: 1, status: "modified" })).toBe("+7 −1");
    expect(fileStatLabel({ path: "a", additions: 0, deletions: 0, status: "modified" })).toBe("no line changes");
  });
});
