// M10-A3 — cost/spend API client for the /dashboard/cost page.
// Lives next to the page (node A3's file boundary); talks to Section 3's
// model-router service with the same bearer/error conventions as src/lib/api.ts
// (mirrors teamApi.ts next to the settings/team page).
import { ApiError, parseApiError } from "@/lib/api";

// The model-router base is not exported from @/lib/api today; this file owns
// its own constant rather than touching shared code outside this lane.
export const MODELS_BASE =
  process.env.NEXT_PUBLIC_MODELS_BASE_URL ?? "http://localhost:8003";

/** Aggregated spend wire shape returned by GET /api/v1/models/spend
 *  (verified against section3/src/harness_router/ledger.py::summarize_spend). */
export interface SpendTotals {
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
}

export interface SpendByModelEntry {
  model: string;
  costUsd: number;
  tokens: number;
}

export interface SpendByDayEntry {
  /** ISO date, e.g. "2026-08-21" ("unknown" if the row lacked a timestamp). */
  day: string;
  costUsd: number;
}

export interface SpendSummary {
  totals: SpendTotals;
  byModel: SpendByModelEntry[];
  byDay: SpendByDayEntry[];
}

export interface SpendQuery {
  teamId?: string;
  sinceDays?: number;
}

async function request<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
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

/** Fetch aggregated spend. The endpoint returns zeroed totals (empty arrays)
 *  when nothing was recorded, and 503 LEDGER_UNAVAILABLE when the cost-ledger
 *  pool is not wired — callers should render those states, not crash. */
export function fetchSpend(query: SpendQuery, token: string): Promise<SpendSummary> {
  const params = new URLSearchParams();
  if (query.teamId) params.set("teamId", query.teamId);
  if (query.sinceDays !== undefined) params.set("sinceDays", String(query.sinceDays));
  const qs = params.toString();
  return request(`${MODELS_BASE}/api/v1/models/spend${qs ? `?${qs}` : ""}`, token);
}

/** True when the endpoint reports no spend at all for the period. */
export function spendIsEmpty(summary: SpendSummary): boolean {
  return (
    summary.byModel.length === 0 &&
    summary.byDay.length === 0 &&
    summary.totals.inputTokens === 0 &&
    summary.totals.outputTokens === 0 &&
    summary.totals.costUsd === 0
  );
}

/** "$0.0000" formatting per the dashboard spec (ledger rounds to 6 decimals;
 *  the UI shows 4). */
export function formatUsd(costUsd: number): string {
  return `$${(Number.isFinite(costUsd) ? costUsd : 0).toFixed(4)}`;
}

/** Humanize token counts: 950 → "950", 12_300 → "12.3k", 4_500_000 → "4.5M". */
export function formatTokens(tokens: number): string {
  const n = Number.isFinite(tokens) ? Math.max(0, tokens) : 0;
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${trimZeros((n / 1000).toFixed(1))}k`;
  return `${trimZeros((n / 1_000_000).toFixed(2))}M`;
}

function trimZeros(s: string): string {
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}
