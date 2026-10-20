"use client";

// Part 47 inbox, presentational half (hook-free so tests can drive it): cursor-paged list, filters,
// mark read / snooze / dismiss, mark all read, delivery state. Urgency is the WORD "Urgent", never colour alone.
import * as React from "react";
import { Button } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { Pill } from "@/components/ui";
import { parkFocus } from "@/lib/focus";
import { formatDateTime, formatRelative } from "@/lib/intl";
import { KINDS, type NotificationItem } from "./notificationsApi";
import {
  FILTER_LABEL, type InboxFilter, SNOOZE_OPTIONS, deliveryText, kindLabel, previewText,
} from "./notificationsModel";

const muted = { color: "var(--muted)" } as const;

export interface InboxActions {
  open: (n: NotificationItem) => void;
  markRead: (n: NotificationItem) => void;
  dismiss: (n: NotificationItem) => void;
  snooze: (n: NotificationItem, minutes: number) => void;
  toggleSnooze: (id: string | null) => void;
  readAll: () => void;
  more: () => void;
  setFilter: (f: InboxFilter) => void;
  setKind: (k: string) => void;
}

const stateText = (n: NotificationItem) =>
  n.state === "snoozed" ? `Snoozed until ${formatDateTime(n.snoozedUntil)}` : n.state === "unread" ? "Unread" : n.state === "read" ? "Read" : "Dismissed";

export function InboxView(p: {
  items: NotificationItem[]; nextCursor: string | null; filter: InboxFilter; kind: string; busy: boolean; loading: boolean;
  message: { tone: "info" | "danger"; text: string } | null; snoozeFor: string | null; now?: number; actions: InboxActions;
}) {
  const a = p.actions;
  const unreadHere = p.items.filter((n) => n.state === "unread").length;
  return (
    <section aria-label="Notifications inbox" tabIndex={-1} data-focus-park="" data-testid="inbox">
      <p style={muted}>
        Nothing here makes a sound, pops up or opens by itself. New items only raise the count on the bell.
        The wording is fixed and never repeats what was said in a comment or plan.
      </p>
      {p.message && <Notice tone={p.message.tone} role={p.message.tone === "danger" ? "alert" : "status"} testId="inbox-message">{p.message.text}</Notice>}

      <div role="group" aria-label="Filter and bulk actions" style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", margin: "8px 0" }}>
        <label>Show{" "}
          <select value={p.filter} onChange={(e) => a.setFilter(e.target.value as InboxFilter)}>
            {(Object.keys(FILTER_LABEL) as InboxFilter[]).map((f) => <option key={f} value={f}>{FILTER_LABEL[f]}</option>)}
          </select>
        </label>
        <label>Type{" "}
          <select value={p.kind} onChange={(e) => a.setKind(e.target.value)}>
            <option value="">All types</option>
            {KINDS.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}
          </select>
        </label>
        <Button size="sm" disabled={p.busy} onClick={(e) => { parkFocus(e); a.readAll(); }}>
          {p.kind ? `Mark all ${kindLabel(p.kind).toLowerCase()} read` : "Mark all read"}
        </Button>
      </div>

      {p.items.length === 0 && !p.loading && <p style={muted} data-testid="inbox-empty">{p.filter === "active" ? "You are all caught up." : "Nothing matches this filter."}</p>}
      {p.loading && p.items.length === 0 && <p style={muted}>Loading…</p>}
      <ul aria-label="Notifications" style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {p.items.map((n) => {
          const text = previewText(n.summaryCode, n.count);
          const id = `n-${n.id}`;
          return (
            <li key={n.id} data-testid={`notification-${n.id}`} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 8, marginBottom: 6, fontWeight: n.state === "unread" ? 600 : 400 }}>
              <div id={id}>
                {n.urgent && <><Pill tone="red">Urgent</Pill>{" "}</>}
                <span data-testid={`preview-${n.id}`}>{text}</span>{" "}
                <span style={{ ...muted, fontWeight: 400 }}>
                  {kindLabel(n.kind)}, <time dateTime={n.updatedAt ?? undefined} title={formatDateTime(n.updatedAt)}>{formatRelative(n.updatedAt, p.now)}</time>. {stateText(n)}.
                </span>
              </div>
              <p style={{ ...muted, margin: "2px 0 4px", fontWeight: 400, fontSize: 12 }}>{deliveryText(n.delivery)}.</p>
              <div role="group" aria-label={`Actions for: ${text}`} style={{ display: "flex", gap: 6, flexWrap: "wrap", fontWeight: 400 }}>
                <Button size="sm" variant="primary" aria-describedby={id} onClick={() => a.open(n)}>Open</Button>
                {n.state !== "read" && n.state !== "dismissed" && <Button size="sm" disabled={p.busy} aria-describedby={id} onClick={(e) => { parkFocus(e); a.markRead(n); }}>Mark read</Button>}
                {n.state !== "dismissed" && <Button size="sm" disabled={p.busy} aria-describedby={id} aria-expanded={p.snoozeFor === n.id} onClick={() => a.toggleSnooze(p.snoozeFor === n.id ? null : n.id)}>Snooze</Button>}
                {n.state !== "dismissed" && <Button size="sm" variant="danger" disabled={p.busy} aria-describedby={id} onClick={(e) => { parkFocus(e); a.dismiss(n); }}>Dismiss</Button>}
              </div>
              {p.snoozeFor === n.id && (
                <div role="group" aria-label="Snooze for" onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); a.toggleSnooze(null); } }}
                  style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6, fontWeight: 400 }}>
                  {SNOOZE_OPTIONS.map((o) => <Button key={o.minutes} size="sm" disabled={p.busy} onClick={(e) => { parkFocus(e); a.snooze(n, o.minutes); }}>{o.label}</Button>)}
                  <Button size="sm" onClick={() => a.toggleSnooze(null)}>Cancel</Button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {p.nextCursor && <p><Button size="sm" disabled={p.busy || p.loading} onClick={a.more}>Load more</Button></p>}
      <p style={{ ...muted, fontSize: 12 }}>{unreadHere > 0 ? `${unreadHere} unread in this list. ` : ""}Preferences, quiet hours and muting are under <a href="/settings/notifications" style={{ color: "var(--accent)" }}>Settings, Notifications</a>.</p>
    </section>
  );
}
