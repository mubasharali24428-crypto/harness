import type { Principal } from "@contracts/roles";

const STORAGE_KEY = "harness.session";

// Loose keys used by the ported S9 pages. Kept identical so existing tabs and
// the live demo stack keep working across the consolidation.
export const KEYS = {
  projectId: "harness.projectId",
  runId: "harness.runId",
  userId: "harness.userId",
  prId: "harness.prId",
  goal: "harness.goal",
  principal: "harness.principal",
} as const;

export interface Session {
  token: string;
  principal: Principal;
}

export function saveSession(token: string, principal: Principal): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ token, principal }));
}

export function loadSession(): Session | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Session;
  } catch {
    return null;
  }
}

export function clearSession(): void {
  if (typeof window === "undefined") return;
  // M12-E2 sign-out hygiene: wipe the session token AND every loose harness.*
  // key (projectId, runId, prId, goal, userId, principal) so a stale run/PR
  // selection never leaks into the next account that signs in on this browser.
  // Sweep by prefix rather than enumerating KEYS so keys added later are
  // covered without touching this function again; uses the Storage interface's
  // own length/key(i) enumeration, which works on real browsers and any
  // spec-compliant mock alike.
  const sweep: string[] = [];
  for (let i = 0; i < window.localStorage.length; i += 1) {
    const key = window.localStorage.key(i);
    if (key && key.startsWith("harness.")) sweep.push(key);
  }
  for (const key of sweep) window.localStorage.removeItem(key);
}

/** Session token or null — the one accessor pages should use. */
export function getToken(): string | null {
  return loadSession()?.token ?? null;
}

function get(key: string): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(key);
}

function set(key: string, value: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(key, value);
}

export function getProjectId(): string | null {
  return get(KEYS.projectId);
}
export function setProjectId(id: string): void {
  set(KEYS.projectId, id);
}
export function getRunId(): string | null {
  return get(KEYS.runId);
}
export function setRunId(id: string): void {
  set(KEYS.runId, id);
}
/** userId falls back to the session principal's id when the loose key is unset. */
export function getUserId(): string | null {
  return get(KEYS.userId) ?? loadSession()?.principal.userId ?? null;
}
export function setUserId(id: string): void {
  set(KEYS.userId, id);
}
export function getPrId(): string | null {
  return get(KEYS.prId);
}
export function setPrId(id: string): void {
  set(KEYS.prId, id);
}
export function getGoal(): string | null {
  return get(KEYS.goal);
}
export function setGoal(goal: string): void {
  set(KEYS.goal, goal);
}
