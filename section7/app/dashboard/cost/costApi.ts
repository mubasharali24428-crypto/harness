// M10-A3 — cost/spend API client for the /dashboard/cost page.
// Lives next to the page (node A3's file boundary); talks to Section 3's
// model-router service with the same bearer/error conventions as src/lib/api.ts
// (mirrors teamApi.ts next to the settings/team page).
//
// M12-G6: honesty upgrades for empty states + client-side CSV export of the
// byDay series. All new logic is exported as PURE functions (no DOM, no fetch)
// so vitest covers it directly; the page owns the DOM side (blob download).
import { ApiError, parseApiError } from "@/lib/api";
// M13-G5b (raw-14): the export filename carries a slugified team name.
import { teamNameSlug } from "@/lib/format";

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

/**
 * M12-G6 (H-011): distinguish zero-because-nothing-ran from zero-because-unwired.
 *
 * The frozen /spend contract gives the UI exactly one hard signal:
 *   - HTTP 503 {code: "LEDGER_UNAVAILABLE"} → metering is NOT wired; every
 *     number on this dashboard would be fabricated, so show the explainer.
 *   - HTTP 200 → the ledger IS connected and queryable, so zeroed totals mean
 *     no usage was RECORDED for the period. That is still not full proof that
 *     metering reaches every agent run (runners can bypass recording while the
 *     ledger itself is healthy), so the page defaults those zeros to the
 *     explainer too — with copy noting activity was genuinely not recorded.
 *
 * "pending" covers first-load/error states the page renders its own way
 * (loading indicator / role="alert"); it never shows numbers or the explainer.
 */
export type SpendView = "unwired" | "no-recorded-activity" | "data" | "pending";

export function classifySpendView(
  summary: SpendSummary | null,
  unwired: boolean,
  hasError: boolean,
): SpendView {
  if (hasError || summary === null) return "pending";
  if (unwired) return "unwired";
  return spendIsEmpty(summary) ? "no-recorded-activity" : "data";
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

/** RFC 4180 single-field escape: quote, then double any embedded quotes. */
function csvField(value: string): string {
  return `"${value.replace(/"/g, '""')}"`;
}

/**
 * Serialize the byDay series as RFC 4180 CSV (all fields quoted; embedded
 * quotes doubled; CRLF row endings). Pure — no Blob/DOM here, so it is
 * unit-testable under vitest's node environment.
 */
export function buildSpendCsv(byDay: SpendByDayEntry[]): string {
  const lines = ["day,cost_usd"];
  for (const d of byDay) {
    lines.push(`${csvField(d.day)},${csvField(formatUsd(d.costUsd))}`);
  }
  return `${lines.join("\r\n")}\r\n`;
}

/**
 * Filename for the byDay export. M13-G5b (raw-14): carries a team-name slug so
 * exports from different teams are distinguishable at a glance, e.g.
 * harness-spend-acme-robotics-20260821T101500Z.csv. A blank/unavailable name
 * falls back to the literal "team" marker rather than dropping the segment.
 */
export function csvFileName(now: Date = new Date(), teamName?: string): string {
  const stamp = now.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
  const slug = teamNameSlug(teamName ?? "") || "team";
  return `harness-spend-${slug}-${stamp}.csv`;
}

/**
 * Trigger a client-side download of `byDay` as CSV via an object URL.
 * Returns the generated filename. DOM-side counterpart of buildSpendCsv.
 */
export function downloadSpendCsv(byDay: SpendByDayEntry[], teamName?: string): string {
  const blob = new Blob([buildSpendCsv(byDay)], { type: "text/csv;charset=utf-8" });
  const name = csvFileName(new Date(), teamName);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return name;
}

/** Docs link shown in the metering explainer (STATUS.md lives at repo root). */
export const COST_DOCS_URL =
  "https://github.com/mubasharali24428-crypto/harness/blob/master/STATUS.md";

function trimZeros(s: string): string {
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}
