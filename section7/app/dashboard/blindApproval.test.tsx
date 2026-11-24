// X-07 KPI tile (node env, static render) + the approve body carrying the measured signal.
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { approvePr } from "@/lib/api";
import { BlindApprovalView, type BlindApprovalKpi } from "./BlindApprovalTile";

afterEach(() => vi.unstubAllGlobals());

const b = (withSignal: number, blind: number, rate: number | null, unknownSignal = 0) =>
  ({ decisions: withSignal + unknownSignal, approvals: withSignal + unknownSignal, withSignal, blind, unknownSignal, rate });

describe("blind-approval tile", () => {
  it("shows a rate only above the minimum sample; unknown stays unknown", () => {
    const kpi: BlindApprovalKpi = { window: 500, minSample: 10, overall: b(12, 3, 0.25, 4),
      byRendering: { technical: b(0, 0, null, 4), plain: b(10, 3, 0.3), decision: b(2, 0, null), unknown: b(0, 0, null) } };
    const h = renderToStaticMarkup(<BlindApprovalView kpi={kpi} />);
    expect(h).toContain("Overall: <strong>25%</strong>");
    expect(h).toMatch(/kpi-plain.*30%/);
    expect(h).toMatch(/kpi-decision.*insufficient data \(2 of 10 measured approvals\)/);
    expect(h).toMatch(/kpi-technical.*<td>4<\/td><\/tr>/); // not-measured count shown, not blind
  });
});

describe("approvePr", () => {
  it("sends viewed_detail when measured and omits it when not", async () => {
    const fn = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fn);
    await approvePr("pr1", "u", "t", false);
    await approvePr("pr1", "u", "t");
    const bodies = fn.mock.calls.map((c) => JSON.parse(((c as unknown as [string, RequestInit])[1]).body as string));
    expect(bodies).toEqual([{ reviewer_user_id: "u", viewed_detail: false }, { reviewer_user_id: "u" }]);
  });
});
