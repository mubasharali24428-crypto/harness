// Mission 10 node B2 — billing API client for the /settings/billing page.
// Client side of interface contract M10-C3 (/api/v1/billing, bearer auth):
//   GET /api/v1/billing/state → {enabled, plan, quota:{monthlyBudgetUsd, usedUsd}, source}
//     (enabled=false + source:"flag-off" when HARNESS_BILLING is unset)
//   PUT /api/v1/billing/plan {plan} → updated state (admin-only)
// Lives next to the page (B2's file boundary); talks to Section 1's identity
// service with the same bearer/error conventions as src/lib/api.ts and
// app/settings/team/teamApi.ts.
import { ApiError, IDENTITY_BASE, parseApiError } from "@/lib/api";

/** M10-C3 quota block: spend vs the team's monthly budget, in USD. */
export interface BillingQuotaDto {
  monthlyBudgetUsd: number;
  usedUsd: number;
}

/** Where the billing state comes from. "flag-off" = HARNESS_BILLING unset. */
export type BillingSource = "flag-off" | "local" | "stripe";

/**
 * M10-C3 GET /api/v1/billing/state. When billing is disabled the backend
 * answers {enabled:false, plan:null, quota:null, source:"flag-off"} — callers
 * render the upsell card, never an error. `plan`/`quota` are null until
 * billing has been enabled and a plan with a budget has been set.
 */
export interface BillingStateDto {
  enabled: boolean;
  plan?: string | null;
  quota: BillingQuotaDto | null;
  source: BillingSource;
  limits?: TeamBillingLimitsDto;
}

export interface TeamBillingLimitsDto {
  teamId: string;
  monthlyBudgetUsd: number;
  maxConcurrentRuns: number;
  budgetAlertPercent: number;
  concurrentRunsOverride: number | null;
  budgetAlertPercentOverride: number | null;
  canManage: boolean;
}

/**
 * Plan choices offered by the change-plan select. Mirrors the server-side
 * Literal("free", "pro", "team") so admins can never submit a value the
 * backend rejects.
 */
export const BILLING_PLANS = ["free", "pro", "team"] as const;

async function request<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${IDENTITY_BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
      ...(init?.headers ?? {}),
    },
  });
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!res.ok) {
    const { message, code } = parseApiError(res.status, body);
    throw new ApiError(message, code, res.status);
  }
  return body as T;
}

export function getBillingState(token: string): Promise<BillingStateDto> {
  return request("/api/v1/billing/state", token);
}

export function updateBillingLimits(
  limits: Pick<TeamBillingLimitsDto, "monthlyBudgetUsd" | "maxConcurrentRuns" | "budgetAlertPercent">,
  token: string,
): Promise<BillingStateDto> {
  return request("/api/v1/billing/plan", token, {
    method: "PUT",
    body: JSON.stringify(limits),
  });
}

/** M10-C3 PUT /api/v1/billing/plan {plan} — admin-only; resolves to updated state. */
export function updatePlan(plan: string, token: string): Promise<BillingStateDto> {
  return request("/api/v1/billing/plan", token, {
    method: "PUT",
    body: JSON.stringify({ plan }),
  });
}

// ---- pure helpers (unit-tested in ./billingApi.test.ts) --------------------

/** True when real spend has passed a positive budget. A budget of 0 (or less)
 *  is treated as "unbounded", never over. */
export function isOverBudget(quota: BillingQuotaDto): boolean {
  return quota.monthlyBudgetUsd > 0 && quota.usedUsd > quota.monthlyBudgetUsd;
}

/** Raw percent of the budget consumed — may exceed 100 when over budget.
 *  NaN signals "no usable budget" (monthlyBudgetUsd <= 0). */
export function rawUsagePct(quota: BillingQuotaDto): number {
  if (!(quota.monthlyBudgetUsd > 0)) return Number.NaN;
  return (quota.usedUsd / quota.monthlyBudgetUsd) * 100;
}

/** Meter percent clamped to [0, 100] for CSS width / aria-valuenow.
 *  0 when there is no usable budget (nothing to fill toward). */
export function meterPct(quota: BillingQuotaDto): number {
  const raw = rawUsagePct(quota);
  if (!Number.isFinite(raw)) return 0;
  return Math.min(100, Math.max(0, raw));
}

export function formatUsd(value: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}
