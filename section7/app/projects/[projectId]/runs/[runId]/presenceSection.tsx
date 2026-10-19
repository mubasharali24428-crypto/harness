"use client";

// Part 41 UI: roster chips with the server's own status sentence and coarse last-seen, participation
// controls, a heartbeat while this tab is visible, and a polite live region for changes.
import * as React from "react";
import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { AnnounceContext } from "@/components/LiveAnnouncer";
import { ApiError } from "@/lib/api";
import { Button } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { type ParticipationAction, type PresenceEntry, type PresenceView, getPresence, leavePresence, participation, sendHeartbeat } from "./controlApi";
import { useAction, usePolled } from "./usePolled";

const muted = { color: "var(--muted)" } as const;

const LAST_SEEN: Record<string, string> = {
  under_5_min: "last seen under 5 minutes ago",
  under_1_hour: "last seen under an hour ago",
  under_1_day: "last seen under a day ago",
};
export const lastSeenText = (e: PresenceEntry): string =>
  (e.presence?.lastSeen && LAST_SEEN[e.presence.lastSeen]) || "";

/** Sentences for other people's status changes between two polls (never for the first load or for yourself). */
export function changedAnnouncements(prev: PresenceEntry[] | null, next: PresenceEntry[]): string[] {
  if (!prev) return [];
  const before = new Map(prev.map((e) => [e.userId, e.announcement]));
  return next.filter((e) => !e.self && before.get(e.userId) !== e.announcement).map((e) => e.announcement);
}

/** One id per browser tab, so a second tab or device is a separate presence row. */
export function tabDeviceId(): string {
  const fresh = () => `tab-${crypto.randomUUID()}`;
  try {
    const k = "harness.presence.deviceId";
    return sessionStorage.getItem(k) ?? (() => { const id = fresh(); sessionStorage.setItem(k, id); return id; })();
  } catch { return fresh(); }
}

export function PresenceRoster(p: {
  data: PresenceView; said: string; error: string | null; busy: boolean; left: boolean;
  onAction: (a: ParticipationAction) => void;
}) {
  const me = p.data.participants.find((e) => e.self);
  return (
    <section aria-label="Presence" data-testid="presence-roster">
      <div role="status" aria-live="polite" aria-atomic="true" data-testid="presence-live" style={{ position: "absolute", left: -9999 }}>{p.said}</div>
      {p.error && <Notice tone="danger" role="alert">{p.error}</Notice>}
      <h3 style={{ fontSize: 13 }}>Who is here</h3>
      {p.data.mode === "off"
        ? <p style={muted}>Your team has turned presence off.</p>
        : <p style={muted}>{p.data.notice}</p>}
      <ul aria-label="Presence roster" style={{ display: "flex", gap: 6, flexWrap: "wrap", listStyle: "none", margin: 0, padding: 0 }}>
        {p.data.participants.map((e) => (
          <li key={e.userId} data-testid={`presence-${e.userId}`}
            style={{ border: "1px solid var(--border)", borderRadius: 12, padding: "2px 10px", fontSize: 13 }}>
            <strong>{e.userId}</strong>{e.self ? " (you)" : ""} ({e.role}): {e.announcement}
            {lastSeenText(e) ? <span style={muted}>, {lastSeenText(e)}</span> : null}
          </li>
        ))}
      </ul>
      <div role="group" aria-label="Your participation" style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 6 }}>
        {me?.role === "participant" && <Button size="sm" disabled={p.busy} onClick={() => p.onAction("observe")}>Observe only</Button>}
        {me?.role === "observer" && <Button size="sm" disabled={p.busy} onClick={() => p.onAction("participate")}>Take part</Button>}
        {me && me.role !== "leader" && <Button size="sm" disabled={p.busy} onClick={() => p.onAction("leave")}>Leave run</Button>}
        {!me && p.left && <Button size="sm" disabled={p.busy} onClick={() => p.onAction("rejoin")}>Rejoin run</Button>}
        {me && (me.preferences?.muted
          ? <Button size="sm" disabled={p.busy} onClick={() => p.onAction("unmute")}>Turn notifications on</Button>
          : <Button size="sm" disabled={p.busy} onClick={() => p.onAction("mute")}>Mute notifications</Button>)}
      </div>
      {me?.role === "leader" && <p style={muted}>You lead this run. Hand the lead to a teammate before you observe or leave.</p>}
    </section>
  );
}

export function PresenceSection({ runId, token, tick, onChanged }: {
  runId: string; token: string; tick: number; onChanged: () => void;
}) {
  const [data, reload] = usePolled(useCallback(() => getPresence(runId, token), [runId, token]), tick, 10000);
  const after = useCallback(async () => { await reload(); onChanged(); }, [reload, onChanged]);
  const { busy, error, said, setSaid, run } = useAction(after);
  const [left, setLeft] = useState(false);
  const announce = useContext(AnnounceContext); // inside the shared panel the one throttled region speaks
  useEffect(() => { if (announce && said) announce(said); }, [announce, said]);

  // Other people's status changes -> live region.
  const prev = useRef<PresenceEntry[] | null>(null);
  useEffect(() => {
    if (!data) return;
    const lines = changedAnnouncements(prev.current, data.participants);
    if (lines.length) setSaid(lines.join(". "));
    prev.current = data.participants;
  }, [data, setSaid]);

  // Heartbeat while joined, presence is on and this tab is visible; leave on unmount.
  const joined = !!data?.participants.some((e) => e.self);
  const enabled = joined && data?.mode !== "off";
  useEffect(() => {
    if (!enabled) return;
    const id = tabDeviceId();
    let sent = false;
    let stopped = false;
    let delay = 30000;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const beat = async () => {
      clearTimeout(timer);
      if (document.visibilityState === "visible") {
        try {
          const r = await sendHeartbeat(runId, token, id);
          sent = true;
          delay = Math.max(5000, r.heartbeatEverySeconds * 1000);
        } catch (e) {
          // Presence off or not a member any more: stop quietly; the next roster poll explains why.
          if (e instanceof ApiError && (e.code === "PRESENCE_DISABLED" || e.code === "NOT_PARTICIPANT")) stopped = true;
        }
      }
      if (!stopped) timer = setTimeout(() => void beat(), delay);
    };
    const onVisible = () => { if (document.visibilityState === "visible") void beat(); };
    document.addEventListener("visibilitychange", onVisible);
    void beat();
    return () => {
      stopped = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      if (sent) void leavePresence(runId, token, id).catch(() => undefined);
    };
  }, [enabled, runId, token]);

  if (!data) return null;
  return (
    <PresenceRoster
      data={data} said={announce ? "" : said} error={error} busy={busy} left={left}
      onAction={(a) => void run(async () => {
        const r = await participation(runId, token, a);
        if (a === "leave") setLeft(true);
        if (a === "rejoin") setLeft(false);
        return r;
      }, (r) => (r as { announcement?: string }).announcement)}
    />
  );
}
