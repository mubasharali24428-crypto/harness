// Mission 10 node B3 — onboarding checklist client for the /onboarding page.
// Client side of the Section 10 onboarding service: GET
// /api/v1/onboarding/checklist → {items:[{id,title,description?,done}]}.
// Lives next to the page (B3's file boundary); same bearer/error conventions as
// src/lib/api.ts and app/settings/billing/billingApi.ts.
//
// NOTE (honest scope): unlike /api/v1/keys or /api/v1/billing, the onboarding
// checklist route authenticates nobody today — Section 10's service exposes it
// without auth (verified against harness_onboarding/api.py), so no bearer token
// is sent. If the deployment later fronts it with an authenticated gateway,
// add the authorization header here, mirroring billingApi.ts.
import { ApiError, parseApiError } from "@/lib/api";

/** Base of the Section 10 onboarding service (deploy wiring may override). */
export const ONBOARDING_BASE =
  process.env.NEXT_PUBLIC_ONBOARDING_BASE_URL ?? "http://localhost:8010";

/** One row of GET /api/v1/onboarding/checklist — camelCase per api-conventions. */
export interface ChecklistItemDto {
  id: string;
  title: string;
  description?: string | null;
  done: boolean;
}

/** Body of GET /api/v1/onboarding/checklist. */
export interface ChecklistDto {
  items: ChecklistItemDto[];
}

/**
 * Real fetch of the first-run checklist. Throws ApiError carrying the frozen
 * contract error envelope ({error:{code,message}}) on non-2xx responses.
 */
export async function getChecklist(base: string = ONBOARDING_BASE): Promise<ChecklistDto> {
  const res = await fetch(`${base}/api/v1/onboarding/checklist`);
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
  return body as ChecklistDto;
}

// ---- pure helpers (unit-tested in ./onboardingApi.test.ts) -----------------

/** Number of checklist rows marked done. */
export function completedCount(items: ChecklistItemDto[]): number {
  return items.filter((i) => i.done).length;
}

/** Progress percent clamped to [0,100]; an empty checklist is 0%, never NaN. */
export function progressPct(items: ChecklistItemDto[]): number {
  if (items.length === 0) return 0;
  return Math.min(100, Math.max(0, Math.round((completedCount(items) / items.length) * 100)));
}

/** Human label for the progress readout, e.g. "2/6 steps complete". */
export function formatProgressLabel(items: ChecklistItemDto[]): string {
  return `${completedCount(items)}/${items.length} steps complete`;
}
