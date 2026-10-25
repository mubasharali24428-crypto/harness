// Team model preferences (Settings -> Models and the composer chip).
//
// Wire: Section 3's GET/PUT /api/v1/models/prefs (migration 0062 keeps the
// choice across restarts) and GET /api/v1/models/catalog for the options.
// The reasoning effort is stored for every deployment but only sent to
// OpenRouter-style endpoints, and the server tells us which case we are in.

import { ApiError, parseApiError } from "@/lib/api";

export const MODELS_BASE =
  process.env.NEXT_PUBLIC_MODELS_BASE_URL ?? "http://localhost:8003";

export type ReasoningEffort = "off" | "low" | "medium" | "high";

export const REASONING_EFFORTS: ReasoningEffort[] = ["off", "low", "medium", "high"];
export const REASONING_LABEL: Record<ReasoningEffort, string> = {
  off: "Off",
  low: "Low",
  medium: "Medium",
  high: "High",
};

export interface ModelPrefs {
  teamId: string;
  primaryModel: string;
  reviewerModel: string;
  reasoningEffort: ReasoningEffort;
  /** false = no persisted row yet (deployment defaults are in effect). */
  persisted: boolean;
  /** false = the endpoint does not accept a reasoning object. */
  reasoningWireSupported: boolean;
  updatedAt: string | null;
}

export interface CatalogProvider {
  name: string;
  kind: string;
  models: string[];
}

async function request<T>(url: string, token: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
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

export function fetchModelPrefs(token: string): Promise<ModelPrefs> {
  return request(`${MODELS_BASE}/api/v1/models/prefs`, token);
}

export function saveModelPrefs(
  token: string,
  body: Pick<ModelPrefs, "primaryModel" | "reviewerModel" | "reasoningEffort">,
): Promise<ModelPrefs> {
  return request(`${MODELS_BASE}/api/v1/models/prefs`, token, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function fetchModelCatalog(token: string): Promise<CatalogProvider[]> {
  return request(`${MODELS_BASE}/api/v1/models/catalog`, token);
}

export function canEditModelPrefs(role: string | null | undefined): boolean {
  return role === "admin";
}

/** Provider groups for a <select><optgroup>; unknown models are kept visible. */
export function providerGroups(
  providers: CatalogProvider[],
  current: string[],
): Array<{ name: string; models: string[] }> {
  const seen = new Set<string>();
  const groups = providers
    .map((p) => ({ name: p.name, models: [...p.models] }))
    .filter((g) => g.models.length > 0);
  for (const g of groups) for (const m of g.models) seen.add(m);
  const missing = current.filter((m) => m && !seen.has(m));
  if (missing.length > 0) groups.push({ name: "current", models: missing });
  return groups;
}

/** Short label for the composer chip: "deepseek-reasoner". */
export function shortModel(model: string): string {
  const tail = model.includes("/") ? model.split("/").pop()! : model;
  return tail.length > 28 ? `${tail.slice(0, 27)}…` : tail;
}

/** The note under the controls — states what the choice actually affects. */
export function scopeNote(reasoningWireSupported: boolean): string {
  return reasoningWireSupported
    ? "Applies to runs and model-router calls. Reasoning effort is sent to the provider."
    : "Applies to runs and model-router calls. Reasoning effort is stored but this provider endpoint does not accept it.";
}
