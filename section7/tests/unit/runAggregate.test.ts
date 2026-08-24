// M14-W4 F3b (W4-C2) — aggregate client unit tests.
// Covers: URL/bearer conventions, nullability preservation (null NEVER
// becomes 0), NUMERIC-exact cost formatting, step ordering/labels, and
// per-field degradation on drifted/partial bodies.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  RUN_AGGREGATE_STEPS_MAX,
  type RunAggregateDto,
  formatCostUsdExact,
  getRunAggregate,
  statePillClass,
  stepLabel,
  stepsNewestFirst,
} from "../../app/runs/runsApi";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function jsonRes(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Full W4-C2 body with every enriched field POPULATED. */
function fullBody() {
  return {
    runId: "run-abc",
    state: "pr_opened",
    planId: "plan-1",
    planApproved: true,
    isHumanEditing: false,
    executing: false,
    engineMode: "live",
    prId: "pr-777",
    url: "https://github.com/acme/repo/pull/42",
    costUsd: 12.5,
    steps: [
      { seq: 1, type: "plan_proposed", ts: "2026-08-23T10:00:00Z" },
      { seq: 2, type: "plan_approved", ts: "2026-08-23T10:01:00Z" },
      { seq: 3, type: "execution_started", ts: null },
    ],
  };
}

describe("getRunAggregate", () => {
  it("GETs /api/v1/orchestrator/runs/{id} with bearer auth", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(fullBody()));
    await getRunAggregate("run-abc", "tok-9");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8004/api/v1/orchestrator/runs/run-abc");
    expect(init.headers.authorization).toBe("Bearer tok-9");
  });

  it("URL-encodes the runId", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(fullBody()));
    await getRunAggregate("run a/b", "tok");
    expect(fetchMock.mock.calls[0][0]).toContain(
      "/api/v1/orchestrator/runs/run%20a%2Fb",
    );
  });

  it("preserves populated fields exactly (W4-C2 parity)", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(fullBody()));
    const agg = await getRunAggregate("run-abc", "tok");
    expect(agg.prId).toBe("pr-777");
    expect(agg.url).toBe("https://github.com/acme/repo/pull/42");
    expect(agg.costUsd).toBe(12.5);
    expect(agg.steps).toHaveLength(3);
    expect(agg.state).toBe("pr_opened");
  });

  it("keeps explicit nulls null — never coerces them to zero/empty fakes", async () => {
    const body = fullBody();
    delete (body as Record<string, unknown>).costUsd;
    delete (body as Record<string, unknown>).prId;
    delete (body as Record<string, unknown>).url;
    delete (body as Record<string, unknown>).steps;
    fetchMock.mockResolvedValueOnce(jsonRes(body));
    const agg = await getRunAggregate("run-abc", "tok");
    expect(agg.costUsd).toBeNull(); // NOT 0
    expect(agg.prId).toBeNull();
    expect(agg.url).toBeNull();
    expect(agg.steps).toEqual([]);
  });

  it("maps a server-sent null costUsd to null (honest no-data)", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ ...fullBody(), costUsd: null }));
    const agg = await getRunAggregate("run-abc", "tok");
    expect(agg.costUsd).toBeNull();
  });

  it("degrades non-finite garbage costUsd to null instead of NaN", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonRes({ ...fullBody(), costUsd: "12.500000" }), // string drift
    );
    const agg = await getRunAggregate("run-abc", "tok");
    expect(agg.costUsd).toBeNull();
  });

  it("drops malformed step rows but keeps well-formed ones", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonRes({
        ...fullBody(),
        steps: [
          { seq: 1, type: "plan_proposed", ts: "2026-08-23T10:00:00Z" },
          "garbage",
          null,
          { type: "no-seq", ts: null }, // missing seq → NaN seq, still kept
        ],
      }),
    );
    const agg = await getRunAggregate("run-abc", "tok");
    expect(agg.steps).toHaveLength(2);
    expect(Number.isNaN(agg.steps[1].seq)).toBe(true);
    expect(agg.steps[1].type).toBe("no-seq");
  });

  it("caps client-side at 100 steps even if the server over-sends", async () => {
    const flood = Array.from({ length: 250 }, (_, i) => ({
      seq: i,
      type: "terminal_output",
      ts: null,
    }));
    fetchMock.mockResolvedValueOnce(jsonRes({ ...fullBody(), steps: flood }));
    const agg = await getRunAggregate("run-abc", "tok");
    expect(agg.steps).toHaveLength(RUN_AGGREGATE_STEPS_MAX);
  });

  it("surfaces ApiError with status for the page's 404 handling", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonRes({ error: { code: "NOT_FOUND", message: "nope" } }, 404),
    );
    await expect(getRunAggregate("ghost", "tok")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      code: "NOT_FOUND",
    });
  });
});

describe("formatCostUsdExact", () => {
  it("renders scale-6 money exactly ($X.XXXXXX)", () => {
    expect(formatCostUsdExact(12.5)).toBe("$12.500000");
    expect(formatCostUsdExact(0)).toBe("$0.000000"); // genuine zero is honest
    expect(formatCostUsdExact(0.000001)).toBe("$0.000001");
    expect(formatCostUsdExact(999999.999999)).toBe("$999999.999999");
  });

  it("degrades non-finite input to — never a fake $0", () => {
    expect(formatCostUsdExact(Number.NaN)).toBe("—");
    expect(formatCostUsdExact(Infinity)).toBe("—");
  });
});

describe("statePillClass (pill vocabulary shared with the runs index)", () => {
  it("colors every REAL AgentState like the runs-index map", () => {
    expect(statePillClass("idle")).toBe("pill blue");
    expect(statePillClass("planning")).toBe("pill yellow");
    expect(statePillClass("awaiting_plan_approval")).toBe("pill blue");
    expect(statePillClass("executing")).toBe("pill yellow");
    expect(statePillClass("paused")).toBe("pill yellow");
    expect(statePillClass("awaiting_done_signal")).toBe("pill blue");
    expect(statePillClass("reviewing")).toBe("pill yellow");
    expect(statePillClass("blocked")).toBe("pill red");
    expect(statePillClass("pr_opened")).toBe("pill blue");
    expect(statePillClass("done")).toBe("pill green");
  });

  it("falls through unknown states to the uncolored pill base", () => {
    expect(statePillClass("warp_speed")).toBe("pill");
    expect(statePillClass("")).toBe("pill");
  });
});

describe("stepLabel", () => {
  it("humanizes the contracts EventType vocabulary", () => {
    expect(stepLabel("plan_proposed")).toBe("Plan proposed");
    expect(stepLabel("blocked_awaiting_clarification")).toBe(
      "Blocked — awaiting clarification",
    );
    expect(stepLabel("engine_failed")).toBe("Engine failed");
  });

  it("humanizes unknown types instead of dropping them", () => {
    expect(stepLabel("mystery_event")).toBe("mystery event");
  });
});

describe("stepsNewestFirst", () => {
  it("flips wire order (oldest→newest) to newest-first without mutating", () => {
    const wire: RunAggregateDto["steps"] = [
      { seq: 1, type: "a", ts: null },
      { seq: 2, type: "b", ts: null },
      { seq: 3, type: "c", ts: null },
    ];
    const view = stepsNewestFirst(wire);
    expect(view.map((s) => s.seq)).toEqual([3, 2, 1]);
    expect(wire.map((s) => s.seq)).toEqual([1, 2, 3]); // input untouched
  });

  it("caps the display slice defensively", () => {
    const wire = Array.from({ length: 150 }, (_, i) => ({
      seq: i,
      type: "t",
      ts: null,
    }));
    expect(stepsNewestFirst(wire, 100)).toHaveLength(100);
    // Newest-first means the FIRST shown row is the highest seq.
    expect(stepsNewestFirst(wire, 100)[0].seq).toBe(149);
  });
});
