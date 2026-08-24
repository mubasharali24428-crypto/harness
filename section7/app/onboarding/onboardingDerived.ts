// M12-E7 (raw-04 M1/M2) — pure derivation layer for the onboarding checklist.
//
// raw-04 M1: the checklist goes stale because "done" only reflects signals
// Section 10 persists itself (security-checklist ack). The real-world
// completion conditions live in OTHER sections' APIs — so this module derives
// completion from those live states, client-side, honestly:
//   * create_team     → principal has a team (always true for a signed-in session)
//   * invite_members  → GET /teams/{teamId}/members has >1 member
//   * connect_github  → GET /github/connection reports connected:true
//   * api_key_added   → GET /keys items non-empty
//   * first_run       → GET /orchestrator/runs total > 0
//   * start_meeting   → deliberately NOT derivable today (no list-meetings
//                       endpoint exists anywhere in the repo) — stays honest,
//                       never faked.
// Nothing here pretends progress: an unknown/unfetched signal is null, and a
// step only flips done when its real condition exists. Unknown step ids fall
// back to the server's verdict, so backend-driven steps keep working.

import type { ChecklistItemDto } from "./onboardingApi";
import {
  GITHUB_BASE,
  IDENTITY_BASE,
  ORCHESTRATOR_BASE,
} from "@/lib/api";

/** One probeable real-world condition, keyed by checklist item id. */
export interface SignalState {
  /** null = unknown / not fetched / probe failed — never treated as done. */
  teamExists: boolean | null;
  membersCount: number | null;
  githubConnected: boolean | null;
  keysCount: number | null;
  runsTotal: number | null;
}

export const EMPTY_SIGNALS: SignalState = {
  teamExists: null,
  membersCount: null,
  githubConnected: null,
  keysCount: null,
  runsTotal: null,
};

/**
 * Pure: whether the checklist item's real-world condition exists.
 *
 * Precedence: a derived signal wins when it exists; `null` (unknown) falls back
 * to the server-reported `done`, so Section-10-owned steps (guided_demo ack,
 * informational other_integrations) keep their existing semantics untouched.
 */
export function deriveItemDone(
  itemId: string,
  signals: SignalState,
  serverDone: boolean,
): boolean {
  switch (itemId) {
    case "create_team":
      return signals.teamExists ?? serverDone;
    case "invite_members":
      return (signals.membersCount ?? 0) > 1 ? true : serverDone;
    case "connect_github":
      return signals.githubConnected ?? serverDone;
    case "api_key_added":
      return (signals.keysCount ?? 0) > 0 ? true : serverDone;
    case "first_run":
      return (signals.runsTotal ?? 0) > 0 ? true : serverDone;
    default:
      // Backend-driven or unrecognized ids: trust the server verdict only.
      return serverDone;
  }
}

/** Pure: map one server row through the derivation. */
export function deriveItems(
  items: ChecklistItemDto[],
  signals: SignalState,
): ChecklistItemDto[] {
  return items.map((item) => ({
    ...item,
    done: deriveItemDone(item.id, signals, item.done),
  }));
}

/** Pure: which derived milestone rows exist for the given signals. */
export function activeMilestones(signals: SignalState): string[] {
  const out: string[] = [];
  if ((signals.keysCount ?? 0) > 0) out.push("api_key_added");
  if ((signals.runsTotal ?? 0) > 0) out.push("first_run");
  return out;
}

/** Pure: where the user acts to satisfy each step (deep link into the app). */
export function stepActionHref(itemId: string): string | null {
  switch (itemId) {
    case "connect_github":
      return "/settings"; // GitHub connection card lives on the settings page
    case "create_team":
    case "invite_members":
      return "/settings/team";
    case "api_key_added":
      return "/settings/keys";
    case "start_meeting":
      return "/meeting";
    case "first_run":
      return "/"; // Command Center starts runs
    default:
      return null; // informational/backend-owned steps carry no app surface
  }
}

/** Short verb shown on the per-step action link. */
export function stepActionLabel(itemId: string): string | null {
  switch (itemId) {
    case "connect_github":
      return "Connect GitHub";
    case "create_team":
      return "Open team settings";
    case "invite_members":
      return "Invite members";
    case "api_key_added":
      return "Add a key";
    case "start_meeting":
      return "Open Meeting";
    case "first_run":
      return "Start a run";
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Cheap signal probes. Each returns null on ANY failure (network down, 404,
// auth redirect, malformed body) — an unreadable signal must never read as
// progress. All are single small GETs, fired together once per focus, not on
// an interval.
// ---------------------------------------------------------------------------

async function getText(url: string, token: string | null): Promise<string | null> {
  try {
    const res = await fetch(url, {
      headers: {
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
    });
    if (!res.ok) return null;
    return await res.text();
  } catch {
    return null;
  }
}

function parseJson(text: string | null): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** GET /github/connection → {connected:boolean} (S2). Null on any failure. */
export async function fetchGithubConnected(
  token: string | null,
): Promise<boolean | null> {
  const body = parseJson(await getText(`${GITHUB_BASE}/api/v1/github/connection`, token));
  if (body && typeof body === "object" && "connected" in body) {
    const connected = (body as { connected: unknown }).connected;
    return typeof connected === "boolean" ? connected : null;
  }
  return null;
}

/** GET /keys?teamId= → {items:[...]} (S1). Item count; null on any failure. */
export async function fetchKeysCount(
  teamId: string | undefined,
  token: string | null,
): Promise<number | null> {
  const qs = teamId ? `?teamId=${encodeURIComponent(teamId)}` : "";
  const body = parseJson(await getText(`${IDENTITY_BASE}/api/v1/keys${qs}`, token));
  if (body && typeof body === "object" && "items" in body) {
    return asArray((body as { items: unknown }).items).length;
  }
  return null;
}

/** GET /orchestrator/runs → {items,total,...} (M10-C2). Total; null on failure. */
export async function fetchRunsTotal(token: string | null): Promise<number | null> {
  const body = parseJson(
    await getText(`${ORCHESTRATOR_BASE}/api/v1/orchestrator/runs?limit=1`, token),
  );
  if (body && typeof body === "object" && "total" in body) {
    const total = (body as { total: unknown }).total;
    return typeof total === "number" && Number.isFinite(total) ? total : null;
  }
  return null;
}

/** GET /teams/{teamId}/members → {items:[...]} (S1). Count; null on failure. */
export async function fetchMembersCount(
  teamId: string,
  token: string | null,
): Promise<number | null> {
  const body = parseJson(
    await getText(`${IDENTITY_BASE}/api/v1/teams/${encodeURIComponent(teamId)}/members`, token),
  );
  if (body && typeof body === "object" && "items" in body) {
    return asArray((body as { items: unknown }).items).length;
  }
  return null;
}
