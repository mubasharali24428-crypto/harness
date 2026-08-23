import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, IDENTITY_BASE } from "@/lib/api";
import {
  BillingQuotaDto,
  formatUsd,
  getBillingState,
  isOverBudget,
  meterPct,
  rawUsagePct,
  updatePlan,
} from "./billingApi";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubOk(responseBody: unknown) {
  return vi.fn(async () => ({
    ok: true,
    status: 200,
    text: async () => JSON.stringify(responseBody),
  }));
}

const TOKEN = "tok-b2";

describe("billing client (M10-C3)", () => {
  it("getBillingState GETs /api/v1/billing/state with bearer", async () => {
    const f = stubOk({
      enabled: true,
      plan: "pro",
      quota: { monthlyBudgetUsd: 50, usedUsd: 12.5 },
      source: "local",
    });
    vi.stubGlobal("fetch", f);
    const state = await getBillingState(TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/billing/state`);
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
    expect(state.enabled).toBe(true);
    expect(state.plan).toBe("pro");
    expect(state.quota).not.toBeNull();
    expect(state.quota!.usedUsd).toBe(12.5);
    expect(state.source).toBe("local");
  });

  it("updatePlan PUTs {plan} to /api/v1/billing/plan and returns updated state", async () => {
    const f = stubOk({ enabled: true, plan: "team", quota: {}, source: "local" });
    vi.stubGlobal("fetch", f);
    const state = await updatePlan("team", TOKEN);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${IDENTITY_BASE}/api/v1/billing/plan`);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ plan: "team" });
    expect(state.plan).toBe("team");
  });

  it("surfaces the contract error envelope as ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 403,
        text: async () =>
          JSON.stringify({ error: { code: "FORBIDDEN_ROLE", message: "admin only" } }),
      })),
    );
    await expect(updatePlan("enterprise", TOKEN)).rejects.toMatchObject({
      name: "ApiError",
      code: "FORBIDDEN_ROLE",
      status: 403,
    } satisfies Partial<ApiError>);
  });
});

describe("quota helpers", () => {
  const q = (budget: number, used: number): BillingQuotaDto => ({
    monthlyBudgetUsd: budget,
    usedUsd: used,
  });

  it("isOverBudget only fires on positive budget with real overspend", () => {
    expect(isOverBudget(q(100, 100.01))).toBe(true);
    expect(isOverBudget(q(100, 100))).toBe(false);
    expect(isOverBudget(q(100, 42))).toBe(false);
    // budget 0 or less = unbounded → never "over"
    expect(isOverBudget(q(0, 9999))).toBe(false);
    expect(isOverBudget(q(-5, 9999))).toBe(false);
  });

  it("meterPct clamps to [0,100]; rawUsagePct may exceed 100", () => {
    expect(meterPct(q(200, 50))).toBe(25);
    expect(rawUsagePct(q(200, 300))).toBe(150);
    expect(meterPct(q(200, 300))).toBe(100);
    expect(meterPct(q(200, -10))).toBe(0);
  });

  it("no usable budget (<=0) → meter empty, NaN raw", () => {
    expect(meterPct(q(0, 40))).toBe(0);
    expect(Number.isNaN(rawUsagePct(q(0, 40)))).toBe(true);
    expect(Number.isNaN(rawUsagePct(q(-3, 40)))).toBe(true);
  });

  it("formatUsd renders USD", () => {
    expect(formatUsd(12.5)).toContain("$12.50");
    expect(formatUsd(1234)).toContain("1,234");
  });
});
