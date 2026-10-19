"use client";

// Part 47 inbox, container half: fetches pages by cursor, performs the actions, opens links only through
// GET /link/{id}. `?open=<id>` (the emailed link) is resolved the same way and never auto-focuses anything.
import * as React from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "@/lib/auth";
import { useAnnouncer } from "@/components/LiveAnnouncer";
import { type NotificationItem, dismiss, listInbox, markRead, notificationError, readAll, snooze } from "./notificationsApi";
import { type InboxFilter, filterQuery } from "./notificationsModel";
import { resolveLink } from "./openNotification";
import { InboxView } from "./inboxView";

export function InboxScreen() {
  const router = useRouter();
  const { announce, region } = useAnnouncer();
  const [token, setToken] = useState<string | null>(null);
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [filter, setFilter] = useState<InboxFilter>("active");
  const [kind, setKind] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  const [snoozeFor, setSnoozeFor] = useState<string | null>(null);
  const opened = useRef(false);

  useEffect(() => setToken(getToken()), []);

  const load = useCallback(async (cursor: string | null) => {
    if (!token) return;
    setLoading(true);
    try {
      const page = await listInbox(token, { ...filterQuery(filter), kind: kind || undefined, cursor });
      setItems((prev) => (cursor ? [...prev, ...page.items.filter((n) => !prev.some((x) => x.id === n.id))] : page.items));
      setNext(page.nextCursor);
    } catch (e) {
      setMessage({ tone: "danger", text: notificationError(e) });
    } finally { setLoading(false); }
  }, [token, filter, kind]);
  useEffect(() => { void load(null); }, [load]);

  const open = useCallback(async (n: { id: string }) => {
    if (!token) return;
    const r = await resolveLink(token, n.id);
    if (r.kind === "message") return setMessage({ tone: r.tone, text: r.text });
    void markRead(token, n.id).catch(() => undefined);
    router.push(r.path);
  }, [token, router]);

  // The emailed link: /notifications?open=<id>
  useEffect(() => {
    if (!token || opened.current) return;
    const id = new URLSearchParams(window.location.search).get("open");
    if (!id) return;
    opened.current = true;
    setMessage({ tone: "info", text: "Opening the notification from your email…" });
    void open({ id });
  }, [token, open]);

  const run = async (fn: () => Promise<unknown>, said: string, after?: () => void) => {
    setBusy(true);
    try { await fn(); setMessage(null); announce(said); after?.(); } catch (e) { setMessage({ tone: "danger", text: notificationError(e) }); }
    finally { setBusy(false); }
  };
  const replace = (n: NotificationItem) => setItems((prev) => {
    // In a filtered list an item that no longer matches leaves it.
    const keep = filter === "active" ? n.state !== "dismissed" : filter === "unread" ? n.state === "unread" : n.state === filter;
    return keep ? prev.map((x) => (x.id === n.id ? n : x)) : prev.filter((x) => x.id !== n.id);
  });

  if (!token) return <p style={{ color: "var(--muted)" }}>Sign in to see your notifications.</p>;
  return (
    <>
      {region}
      <InboxView
        items={items} nextCursor={next} filter={filter} kind={kind} busy={busy} loading={loading} message={message} snoozeFor={snoozeFor}
        actions={{
          open: (n) => void open(n),
          markRead: (n) => void run(async () => replace(await markRead(token, n.id)), "Marked as read"),
          dismiss: (n) => void run(async () => replace(await dismiss(token, n.id)), "Dismissed"),
          snooze: (n, m) => void run(async () => replace(await snooze(token, n.id, m)), "Snoozed", () => setSnoozeFor(null)),
          toggleSnooze: setSnoozeFor,
          readAll: () => void run(async () => { const r = await readAll(token, kind || undefined); await load(null); announce(`${r.updated} marked as read`); }, ""),
          more: () => void load(next),
          setFilter: (f) => { setFilter(f); setSnoozeFor(null); },
          setKind: (k) => { setKind(k); setSnoozeFor(null); },
        }}
      />
    </>
  );
}
