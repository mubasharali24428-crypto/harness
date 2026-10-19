"use client";

// Part 47: the bell in the app shell. A plain link to /notifications with the unread count in its
// accessible name. By design it makes no sound, opens nothing, takes no focus and shows no toast: new
// items are only counted, and announced politely and throttled (at most one sentence per few seconds).
import * as React from "react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { getToken } from "@/lib/auth";
import { useAnnouncer } from "@/components/LiveAnnouncer";
import { usePolled } from "../projects/[projectId]/runs/[runId]/usePolled";
import { getUnreadCount } from "./notificationsApi";
import { badgeText, bellLabel, newSince } from "./notificationsModel";

const POLL_MS = 60_000;

export function BellView({ unread, urgent }: { unread: number; urgent: number }) {
  return (
    <Link href="/notifications" className="nav-item" data-testid="notification-bell" aria-label={bellLabel(unread, urgent)}
      style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
      <span aria-hidden="true">Notifications</span>
      {unread > 0 && (
        <span data-testid="notification-badge" aria-hidden="true"
          style={{ minWidth: 22, textAlign: "center", padding: "0 6px", borderRadius: 11, border: `1px solid var(${urgent > 0 ? "--red" : "--accent"})`, fontSize: 12 }}>
          {badgeText(unread)}{urgent > 0 ? "!" : ""}
        </span>
      )}
    </Link>
  );
}

function Bell({ token }: { token: string }) {
  const [visible, setVisible] = useState(0); // bumped when the tab becomes visible again: poll at once
  const load = useCallback(() => {
    // A hidden tab does not poll (usePolled keeps the last count).
    if (typeof document !== "undefined" && document.visibilityState === "hidden") return Promise.reject(new Error("hidden"));
    return getUnreadCount(token);
  }, [token]);
  const [count] = usePolled(load, visible, POLL_MS);
  const { announce, region } = useAnnouncer();
  const prev = useRef<{ unread: number; urgentUnread: number } | null>(null);

  useEffect(() => {
    const on = () => { if (document.visibilityState === "visible") setVisible((v) => v + 1); };
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  useEffect(() => {
    if (!count) return;
    const say = newSince(prev.current, count);
    if (say) announce(say);
    prev.current = count;
  }, [count, announce]);

  return (
    <div data-testid="notification-bell-wrap">
      {region}
      <BellView unread={count?.unread ?? 0} urgent={count?.urgentUnread ?? 0} />
    </div>
  );
}

/** Renders nothing until a session exists (the shell redirects to /login without one). */
export default function NotificationBell() {
  const [token, setToken] = useState<string | null>(null);
  useEffect(() => setToken(getToken()), []);
  return token ? <Bell token={token} /> : null;
}
