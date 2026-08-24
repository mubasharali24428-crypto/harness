// M12-G6 tests — cost dashboard honesty (H-011) + CSV export.
// Covers the three required areas:
//   1. explainer-vs-data branch  → classifySpendView()
//   2. CSV generation content    → buildSpendCsv() / csvFileName()
//      (+ DOM-side downloadSpendCsv via injected stubs)
//   3. preset chip param wiring  → fetchSpend() sinceDays/teamId query string
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MODELS_BASE,
  SpendSummary,
  buildSpendCsv,
  classifySpendView,
  csvFileName,
  downloadSpendCsv,
  fetchSpend,
} from "../../app/dashboard/cost/costApi";

afterEach(() => {
  vi.unstubAllGlobals();
});

function makeSummary(overrides: Partial<SpendSummary> = {}): SpendSummary {
  return {
    totals: { inputTokens: 0, outputTokens: 0, costUsd: 0 },
    byModel: [],
    byDay: [],
    ...overrides,
  };
}

const DATA_SUMMARY = makeSummary({
  totals: { inputTokens: 1200, outputTokens: 3400, costUsd: 0.1234 },
  byModel: [{ model: "deepseek-chat", costUsd: 0.1234, tokens: 4600 }],
  byDay: [
    { day: "2026-08-20", costUsd: 0.05 },
    { day: "2026-08-21", costUsd: 0.0734 },
  ],
});

describe("classifySpendView (H-011 explainer-vs-data branch)", () => {
  it("returns pending before any data has loaded", () => {
    expect(classifySpendView(null, false, false)).toBe("pending");
  });

  it("returns pending when the load errored", () => {
    expect(classifySpendView(null, false, true)).toBe("pending");
    expect(classifySpendView(DATA_SUMMARY, false, true)).toBe("pending");
  });

  it("returns unwired on 503 LEDGER_UNAVAILABLE even if a stale summary exists", () => {
    expect(classifySpendView(DATA_SUMMARY, true, false)).toBe("unwired");
    expect(classifySpendView(makeSummary(), true, false)).toBe("unwired");
  });

  it("defaults all-zero responses to no-recorded-activity (explainer)", () => {
    // HTTP 200 with zeroed totals: ledger reachable but nothing recorded —
    // we cannot prove metering reaches agent runs, so default to explainer.
    const view = classifySpendView(makeSummary(), false, false);
    expect(view).toBe("no-recorded-activity");
    expect(view).not.toBe("data");
  });

  it("zeroed totals stay no-recorded-activity even when arrays carry rows", () => {
    expect(
      classifySpendView(
        makeSummary({ byModel: [], byDay: [] }),
        false,
        false,
      ),
    ).toBe("no-recorded-activity");
  });

  it("returns data for a non-empty summary", () => {
    expect(classifySpendView(DATA_SUMMARY, false, false)).toBe("data");
  });
});

describe("buildSpendCsv", () => {
  it("emits a header row even for an empty series", () => {
    expect(buildSpendCsv([])).toBe("day,cost_usd\r\n");
  });

  it("serializes day + formatted USD per row with CRLF endings", () => {
    const csv = buildSpendCsv([
      { day: "2026-08-20", costUsd: 0.05 },
      { day: "2026-08-21", costUsd: 1.23456 },
    ]);
    expect(csv).toBe('day,cost_usd\r\n"2026-08-20","$0.0500"\r\n"2026-08-21","$1.2346"\r\n');
  });

  it("RFC-4180-escapes embedded quotes (e.g. an unknown-day marker)", () => {
    const csv = buildSpendCsv([{ day: 'say "hi"', costUsd: 0 }]);
    expect(csv).toContain('"say ""hi"""');
  });

  it("rounds costs to the dashboard's 4-decimal display format", () => {
    const csv = buildSpendCsv([{ day: "2026-08-21", costUsd: 0.000012349 }]);
    expect(csv).toBe('day,cost_usd\r\n"2026-08-21","$0.0000"\r\n');
  });
});

describe("csvFileName", () => {
  it("derives a timestamped name from the given date", () => {
    const d = new Date("2026-08-21T10:15:30.123Z");
    // M13-G5b: no team name available → literal "team" marker keeps the segment.
    expect(csvFileName(d)).toBe("harness-spend-team-20260821T101530Z.csv");
  });

  it("carries a slugified team name (M13-G5b raw-14)", () => {
    const d = new Date("2026-08-21T10:15:30.123Z");
    expect(csvFileName(d, "Acme Robotics!")).toBe(
      "harness-spend-acme-robotics-20260821T101530Z.csv",
    );
  });

  it("falls back to the team marker for blank/unavailable names", () => {
    const d = new Date("2026-08-21T10:15:30.123Z");
    expect(csvFileName(d, "")).toBe("harness-spend-team-20260821T101530Z.csv");
    expect(csvFileName(d, "   ")).toBe("harness-spend-team-20260821T101530Z.csv");
  });

  it("keeps the filename filesystem-safe for hostile team names", () => {
    const d = new Date("2026-08-21T10:15:30.123Z");
    const name = csvFileName(d, "../../etc/passwd");
    expect(name).toMatch(/^harness-spend-[a-z0-9-]+-20260821T101530Z\.csv$/);
    expect(name).not.toContain("/");
    expect(name).not.toContain("..");
  });

  it("produces a .csv name for now()", () => {
    expect(csvFileName()).toMatch(/^harness-spend-[a-z0-9]+(?:-[a-z0-9]+)*-\d{8}T\d{6}Z\.csv$/);
  });
});

describe("downloadSpendCsv (DOM side)", () => {
  it("downloads the serialized CSV under a timestamped .csv name", async () => {
    const revoke = vi.fn();
    const createdBlobs: unknown[] = [];
    class StubBlob {
      parts: unknown[];
      type: string;
      constructor(parts: BlobPart[], options?: BlobPropertyBag) {
        this.parts = parts;
        this.type = options?.type ?? "";
      }
    }
    const anchor = {
      href: "",
      download: "",
      click: vi.fn(),
      remove: vi.fn(),
    } as unknown as HTMLAnchorElement;
    const appendChild = vi.fn();
    (globalThis as Record<string, unknown>).Blob = StubBlob;
    (globalThis as Record<string, unknown>).URL = {
      createObjectURL: (blob: unknown) => {
        createdBlobs.push(blob);
        return `blob:fake-${createdBlobs.length}`;
      },
      revokeObjectURL: revoke,
    };
    (globalThis as Record<string, unknown>).document = {
      createElement: (tag: string) => {
        expect(tag).toBe("a");
        return anchor;
      },
      body: { appendChild },
    };

    const name = await downloadSpendCsv([{ day: "2026-08-21", costUsd: 0.42 }]);

    expect(name).toMatch(/^harness-spend-team-\d{8}T\d{6}Z\.csv$/);
    expect(anchor.download).toBe(name);
    expect(anchor.href).toBe("blob:fake-1");
    expect(anchor.click).toHaveBeenCalledTimes(1);
    expect(anchor.remove).toHaveBeenCalledTimes(1);
    expect(appendChild).toHaveBeenCalledWith(anchor);
    expect(revoke).toHaveBeenCalledWith("blob:fake-1");
    const blob = createdBlobs[0] as unknown as { parts: string[]; type: string };
    expect(blob.type).toBe("text/csv;charset=utf-8");
    expect(blob.parts[0]).toBe('day,cost_usd\r\n"2026-08-21","$0.4200"\r\n');
  });
});

describe("fetchSpend query wiring (preset chips → sinceDays)", () => {
  async function captureUrl(query: { teamId?: string; sinceDays?: number }): Promise<string> {
    let requested = "";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        requested = String(input);
        return {
          ok: true,
          status: 200,
          text: async () =>
            JSON.stringify({ totals: { inputTokens: 0, outputTokens: 0, costUsd: 0 }, byModel: [], byDay: [] }),
        };
      }),
    );
    await fetchSpend(query, "tok-123");
    return requested;
  }

  it.each([7, 30, 90])("sends sinceDays=%i for the %id preset chip", async (days) => {
    const url = await captureUrl({ teamId: "team-1", sinceDays: days });
    expect(url).toBe(`${MODELS_BASE}/api/v1/models/spend?teamId=team-1&sinceDays=${days}`);
  });

  it("sends only sinceDays when teamId is absent", async () => {
    const url = await captureUrl({ sinceDays: 7 });
    expect(url).toBe(`${MODELS_BASE}/api/v1/models/spend?sinceDays=7`);
  });

  it("omits the query string entirely when nothing is selected", async () => {
    const url = await captureUrl({});
    expect(url).toBe(`${MODELS_BASE}/api/v1/models/spend`);
  });
});
