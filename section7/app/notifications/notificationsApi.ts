// Part 47 client: the person's notification inbox, preferences and mutes (section1 identity service,
// docs/notifications.md). Own data only; identity is the bearer token. The inbox never plays a sound,
// opens a pop-up or steals focus: the only live signal is GET /unread-count for a badge.
import { ApiError, IDENTITY_BASE, parseApiError } from "@/lib/api";
import { call, post } from "../projects/[projectId]/runs/[runId]/collabApi";
import { mutationError } from "../projects/[projectId]/runs/[runId]/realtimeCore";

export const KINDS = ["approval", "mention", "handoff", "failure", "blocked", "control_request", "suggestion", "budget"] as const;
export type Kind = (typeof KINDS)[number];
export type ItemState = "unread" | "read" | "snoozed" | "dismissed";
export type EmailMode = "off" | "urgent" | "immediate" | "digest";

export interface EmailDelivery { state: string; reason: string | null; attempts: number; dueAt: string | null; lastAttemptAt: string | null }
export interface NotificationItem {
  id: string; kind: string; state: ItemState; urgent: boolean; count: number; summaryCode: string; preview?: string;
  sourceKind: string; sourceId: string; runId: string | null; sessionId: string | null; workspaceId: string | null;
  createdAt: string | null; updatedAt: string | null; snoozedUntil: string | null;
  delivery: { inapp?: string; email?: EmailDelivery }; link: string;
}
export interface InboxPage { items: NotificationItem[]; nextCursor: string | null }
export interface Mute { id: string; scopeKind: string; scopeId: string; until: string | null; createdAt: string | null; endedAt: string | null }
export interface Prefs {
  scope: { kind: string; id: string | null };
  inapp: string | null; email: string | null; events: Partial<Record<string, { inapp?: string; email?: string }>>; version: number;
  timezone?: string; quietHours?: { enabled: boolean; start: string; end: string };
  digest?: { period: string; hour: number; lastSentAt: string | null };
  mutes?: Mute[]; kinds?: string[]; effective?: Record<string, { inapp: string; email: string }>;
}
export type PrefsPatch = Record<string, unknown> & { expectedVersion?: number };

const root = `${IDENTITY_BASE}/api/v1/notifications`;

// ---- stable error codes -> plain language ----
const MESSAGES: Record<string, string> = {
  NOT_FOUND: "That notification, scope or mute no longer exists, or you no longer have access to it.",
  VALIDATION_ERROR: "Some of those values are not allowed. Check them and try again.",
  INVALID_TIMEZONE: "That time zone is not known. Pick one from the list.",
  INVALID_QUIET_HOURS: "Quiet hours need a start and an end that are different times.",
  ILLEGAL_STATE: "A dismissed notification cannot be snoozed.",
  MUTE_CONFLICT: "That is already muted.",
};
export function notificationError(e: unknown): string {
  if (e instanceof ApiError && e.code === "VERSION_CONFLICT") {
    return "Your settings were changed somewhere else. They have been reloaded; make your changes again.";
  }
  return mutationError(e, (x) => (x instanceof ApiError ? (MESSAGES[x.code] ?? x.message) : String((x as Error)?.message ?? x)));
}

// ---- inbox ----
export const getUnreadCount = (t: string) => call<{ unread: number; urgentUnread: number }>(`${root}/unread-count`, t);
export function listInbox(t: string, o: { unread?: boolean; kind?: string; state?: string; cursor?: string | null; limit?: number } = {}) {
  const q = new URLSearchParams({ limit: String(o.limit ?? 30) });
  if (o.unread) q.set("unread", "true");
  if (o.kind) q.set("kind", o.kind);
  if (o.state) q.set("state", o.state);
  if (o.cursor) q.set("cursor", o.cursor);
  return call<InboxPage>(`${root}?${q}`, t);
}
const act = (t: string, id: string, what: "read" | "dismiss", body?: unknown) => post<NotificationItem>(`${root}/${encodeURIComponent(id)}/${what}`, t, body);
export const markRead = (t: string, id: string) => act(t, id, "read");
export const dismiss = (t: string, id: string) => act(t, id, "dismiss");
export const snooze = (t: string, id: string, minutes: number) => post<NotificationItem>(`${root}/${encodeURIComponent(id)}/snooze`, t, { minutes });
export const readAll = (t: string, kind?: string) => post<{ updated: number }>(`${root}/read-all`, t, kind ? { kind } : {});
/** The ONLY way to open a notification: the server re-checks access on every call and answers 404 when it is gone. */
export const getLink = (t: string, id: string) => call<{ notificationId: string; path: string }>(`${root}/link/${encodeURIComponent(id)}`, t);

// ---- preferences and mutes ----
export const getPrefs = (t: string) => call<Prefs>(`${root}/preferences`, t);
export const putPrefs = (t: string, patch: PrefsPatch) => call<Prefs>(`${root}/preferences`, t, { method: "PUT", body: JSON.stringify(patch) });
export const getScopePrefs = (t: string, kind: "workspace" | "session", id: string) =>
  call<Prefs>(`${root}/preferences/scopes/${kind}/${encodeURIComponent(id)}`, t);
export const putScopePrefs = (t: string, kind: "workspace" | "session", id: string, patch: PrefsPatch) =>
  call<Prefs>(`${root}/preferences/scopes/${kind}/${encodeURIComponent(id)}`, t, { method: "PUT", body: JSON.stringify(patch) });
export const muteScope = (t: string, b: { scopeKind: "workspace" | "session" | "run" | "kind"; scopeId: string; minutes?: number }) =>
  post<Mute>(`${root}/mute`, t, b);
export const unmute = (t: string, id: string) => call<unknown>(`${root}/mute/${encodeURIComponent(id)}`, t, { method: "DELETE" });

/** The one route without a session: the emailed token is the credential, so no Authorization header is sent. */
export async function unsubscribe(token: string): Promise<{ unsubscribed: boolean; alreadyApplied: boolean }> {
  const res = await fetch(`${root}/unsubscribe`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ token }) });
  const text = await res.text();
  let body: unknown = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = null; }
  if (!res.ok) {
    const { message, code } = parseApiError(res.status, body);
    throw new ApiError(message, code, res.status);
  }
  return body as { unsubscribed: boolean; alreadyApplied: boolean };
}
