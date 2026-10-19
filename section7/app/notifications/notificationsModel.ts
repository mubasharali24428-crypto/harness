// Part 47 view-model: pure helpers. The preview sentence is derived HERE from the closed summaryCode with a
// fixed English map (same wording as PREVIEWS in section1 notifications.py); an unknown code reads as a
// generic sentence, and no text from the server's rows is ever shown, so a notification cannot carry content.
import { type EmailDelivery, type EmailMode, KINDS, type Kind, type NotificationItem, type Prefs, type PrefsPatch } from "./notificationsApi";
import { formatNumber } from "@/lib/intl";

export const PREVIEWS: Record<string, [string, string]> = {
  approval_requested: ["An approval needs your decision", "{n} approvals need your decision"],
  approval_decided: ["An approval was decided", "{n} approvals were decided"],
  mentioned: ["You were mentioned", "You were mentioned {n} times"],
  handoff_offered: ["A handoff was offered to you", "{n} handoffs were offered to you"],
  handoff_accepted: ["Your handoff was accepted", "{n} handoffs were accepted"],
  handoff_rejected: ["Your handoff was declined", "{n} handoffs were declined"],
  run_failed: ["A run failed", "{n} run failures"],
  review_failed: ["A review failed", "{n} reviews failed"],
  run_blocked: ["A run is blocked and needs input", "{n} run blockers need input"],
  control_requested: ["Someone asked for control of a run", "{n} control requests"],
  control_granted: ["Your control request was granted", "{n} control grants"],
  control_declined: ["Your control request was declined", "{n} control requests declined"],
  suggestion_queued: ["A suggestion is waiting for you", "{n} suggestions are waiting"],
};
const GENERIC: [string, string] = ["You have a notification", "{n} notifications"];

export function previewText(summaryCode: string, count: number, locale?: string): string {
  const [one, many] = Object.hasOwn(PREVIEWS, summaryCode) ? PREVIEWS[summaryCode] : GENERIC;
  return count <= 1 ? one : many.replace("{n}", formatNumber(count, { locale }));
}

export const KIND_LABEL: Record<Kind, string> = {
  approval: "Approvals", mention: "Mentions", handoff: "Handoffs", failure: "Failures", blocked: "Blocked runs",
  control_request: "Control requests", suggestion: "Suggestions",
};
export const kindLabel = (k: string) => (Object.hasOwn(KIND_LABEL, k) ? KIND_LABEL[k as Kind] : "Other");

// ---- the bell ----
/** Accessible name of the bell link: the count is part of the name, never colour or a bare badge. */
export function bellLabel(unread: number, urgent: number, locale?: string): string {
  if (unread <= 0) return "Notifications, none unread";
  const n = formatNumber(unread, { locale });
  return `Notifications, ${n} unread${urgent > 0 ? `, ${formatNumber(urgent, { locale })} urgent` : ""}`;
}
export const badgeText = (unread: number, locale?: string) => (unread > 99 ? `${formatNumber(99, { locale })}+` : formatNumber(unread, { locale }));

/** Polite sentence when the unread count grew since the last poll; null for the first poll or no growth. */
export function newSince(prev: { unread: number; urgentUnread: number } | null, next: { unread: number; urgentUnread: number }): string | null {
  if (!prev || next.unread <= prev.unread) return null;
  const d = next.unread - prev.unread;
  return `${d} new notification${d === 1 ? "" : "s"}${next.urgentUnread > prev.urgentUnread ? ", including an urgent one" : ""}`;
}

// ---- inbox rows ----
export const SNOOZE_OPTIONS: { label: string; minutes: number }[] = [
  { label: "1 hour", minutes: 60 }, { label: "3 hours", minutes: 180 }, { label: "Tomorrow", minutes: 1440 }, { label: "Next week", minutes: 10080 },
];
export const MUTE_OPTIONS: { label: string; minutes?: number }[] = [
  { label: "1 hour", minutes: 60 }, { label: "8 hours", minutes: 480 }, { label: "1 day", minutes: 1440 }, { label: "7 days", minutes: 10080 }, { label: "Until I unmute" },
];

export type InboxFilter = "active" | "unread" | "read" | "snoozed" | "dismissed";
export const FILTER_LABEL: Record<InboxFilter, string> = { active: "Active", unread: "Unread only", read: "Read", snoozed: "Snoozed", dismissed: "Dismissed" };
export function filterQuery(f: InboxFilter): { unread?: boolean; state?: string } {
  return f === "active" ? {} : f === "unread" ? { unread: true } : { state: f };
}

const EMAIL_REASON: Record<string, string> = {
  immediate: "sending shortly", quiet_hours: "held until your quiet hours end", urgent_bypass: "urgent, sent right away", retrying: "retrying after a problem",
  opted_out: "you turned email off", handled_in_app: "you already handled it here", no_recipient: "no email address on file", unsubscribed: "you unsubscribed",
};
/** Delivery in words. In-app is always delivered; email is mentioned only when it is not `none` (docs 008). */
export function deliveryText(d: NotificationItem["delivery"] | undefined, locale?: string): string {
  const parts = ["Delivered in the app"];
  const e: EmailDelivery | undefined = d?.email;
  if (e && e.state !== "none") {
    const why = e.reason ? EMAIL_REASON[e.reason] : undefined;
    const t =
      e.state === "pending" ? `Email waiting${why ? `: ${why}` : ""}` :
      e.state === "digest" ? "Email: will be in your digest" :
      e.state === "sent" ? "Email sent" :
      e.state === "failed" ? `Email failed after ${formatNumber(e.attempts, { locale })} attempts` :
      e.state === "skipped" ? `Email not sent${why ? `: ${why}` : ""}` : `Email ${e.state}`;
    parts.push(t);
  }
  return parts.join(". ");
}

// ---- opening a notification ----
/** A same-site absolute path or null. Never a URL with a scheme or host, never protocol-relative. */
export function safeInternalPath(p: unknown): string | null {
  if (typeof p !== "string" || !p.startsWith("/") || p.startsWith("//") || p.includes("\\") || /[\u0000-\u001f]/.test(p)) return null;
  return p;
}
/** Routes this app has. A path from the server to a page that does not exist here is said so, not navigated to. */
export function hasRoute(path: string): boolean {
  return /^\/(projects\/[^/]+\/runs\/[^/?#]+|notifications)(\?|#|$)/.test(path);
}
export const LINK_GONE = "You can no longer open this: it was removed, or you no longer have access to it.";
export const LINK_NO_PAGE = "This points to a page this version of the app does not have yet.";

// ---- preferences form <-> merge patch ----
export const EMAIL_LABEL: Record<EmailMode, string> = { off: "Never", urgent: "Urgent only", immediate: "Right away", digest: "In a digest" };

export interface PrefsForm {
  inapp: string; email: string; // "" = inherit (scopes) / built-in default (global)
  events: Record<string, { inapp: string; email: string }>;
  timezone: string; quietEnabled: boolean; quietStart: string; quietEnd: string; digestPeriod: string; digestHour: number;
}

export function formFromPrefs(p: Prefs): PrefsForm {
  const events: PrefsForm["events"] = {};
  for (const k of KINDS) events[k] = { inapp: p.events?.[k]?.inapp ?? "", email: p.events?.[k]?.email ?? "" };
  return {
    inapp: p.inapp ?? "", email: p.email ?? "", events,
    timezone: p.timezone ?? "UTC", quietEnabled: p.quietHours?.enabled ?? false, quietStart: p.quietHours?.start ?? "22:00", quietEnd: p.quietHours?.end ?? "07:00",
    digestPeriod: p.digest?.period ?? "daily", digestHour: p.digest?.hour ?? 8,
  };
}

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
export function quietProblem(f: Pick<PrefsForm, "quietEnabled" | "quietStart" | "quietEnd">): string | null {
  if (!f.quietEnabled) return null;
  if (!HHMM.test(f.quietStart) || !HHMM.test(f.quietEnd)) return "Enter the quiet hours as times, for example 22:00 and 07:00.";
  if (f.quietStart === f.quietEnd) return "Quiet hours need a start and an end that are different times.";
  return null;
}

/** Only what changed, as the merge-patch the API takes: an event entry set to null removes it, a null field inherits. */
export function patchFromForm(orig: PrefsForm, cur: PrefsForm, scope: "global" | "workspace" | "session", version: number): PrefsPatch {
  const patch: PrefsPatch = {};
  if (cur.inapp !== orig.inapp) patch.inapp = cur.inapp || null;
  if (cur.email !== orig.email) patch.email = cur.email || null;
  const events: Record<string, { inapp: string | null; email: string | null } | null> = {};
  for (const k of KINDS) {
    const a = orig.events[k], b = cur.events[k];
    if (a.inapp === b.inapp && a.email === b.email) continue;
    events[k] = !b.inapp && !b.email ? null : { inapp: b.inapp || null, email: b.email || null };
  }
  if (Object.keys(events).length) patch.events = events;
  if (scope === "global") {
    if (cur.timezone !== orig.timezone) patch.timezone = cur.timezone;
    if (cur.quietEnabled !== orig.quietEnabled || cur.quietStart !== orig.quietStart || cur.quietEnd !== orig.quietEnd) {
      patch.quietHours = { enabled: cur.quietEnabled, start: cur.quietStart, end: cur.quietEnd };
    }
    if (cur.digestPeriod !== orig.digestPeriod || cur.digestHour !== orig.digestHour) patch.digest = { period: cur.digestPeriod, hour: cur.digestHour };
  }
  if (Object.keys(patch).length && version >= 1) patch.expectedVersion = version;
  return patch;
}
export const isDirty = (patch: PrefsPatch) => Object.keys(patch).filter((k) => k !== "expectedVersion").length > 0;

export function scopeProblem(kind: "workspace" | "session", id: string): string | null {
  if (!id.trim()) return `Enter the ${kind} id.`;
  if (id.trim().length > 80) return "That id is too long.";
  return null;
}

export function muteLabel(m: Pick<{ scopeKind: string; scopeId: string }, "scopeKind" | "scopeId">): string {
  return m.scopeKind === "kind" ? `All ${kindLabel(m.scopeId).toLowerCase()}` : `${m.scopeKind[0].toUpperCase()}${m.scopeKind.slice(1)} ${m.scopeId}`;
}
