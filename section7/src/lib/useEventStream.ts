"use client";

import { useEffect, useRef, useState } from "react";
import type { EventEnvelope } from "@contracts/events";
import { fetchWsTicket, IDENTITY_WS_BASE } from "./api";

const RECONNECT_DELAY_MS = 1500;

/** Pure: merge one incoming envelope into the existing ordered, deduped list.
 *  Dedup by seq guards the reconnect race where the server might re-send an event
 *  whose seq we already have (e.g. a live event that arrived just before a drop). */
export function mergeEvents(existing: EventEnvelope[], incoming: EventEnvelope): EventEnvelope[] {
  if (existing.some((e) => e.seq === incoming.seq)) return existing;
  const next = [...existing, incoming];
  next.sort((a, b) => a.seq - b.seq);
  return next;
}

export type ConnectionState = "connecting" | "open" | "reconnecting" | "closed";

/** Connects to Section 1's per-project event bus WS. The server itself replays every
 *  missed event (by `afterSeq`) before switching to live push on the SAME socket, so a
 *  reconnect just needs to reopen with the highest seq we've already seen — no separate
 *  REST replay call needed for the steady-state reconnect path. */
export function useEventStream(projectId: string | null, token: string | null) {
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const lastSeqRef = useRef(0);
  const socketRef = useRef<WebSocket | null>(null);
  const stoppedRef = useRef(false);

  useEffect(() => {
    if (!projectId || !token) return;
    const pid: string = projectId;
    const tok: string = token;
    stoppedRef.current = false;

    function connect() {
      if (stoppedRef.current) return;
      setConnectionState((prev) => (prev === "open" ? "reconnecting" : "connecting"));
      // H8: tickets are single-use, so EVERY attempt mints a fresh one. The
      // long-lived bearer token never appears in a URL anymore.
      fetchWsTicket(pid, tok)
        .then((ticket) => {
          if (stoppedRef.current) return null;
          const url =
            `${IDENTITY_WS_BASE}/api/v1/ws/${pid}` +
            `?afterSeq=${lastSeqRef.current}&ticket=${encodeURIComponent(ticket)}`;
          const ws = new WebSocket(url);
          socketRef.current = ws;

          ws.onopen = () => setConnectionState("open");

          ws.onmessage = (evt) => {
            const envelope = JSON.parse(evt.data) as EventEnvelope;
            lastSeqRef.current = Math.max(lastSeqRef.current, envelope.seq);
            setEvents((prev) => mergeEvents(prev, envelope));
          };

          ws.onclose = () => {
            if (stoppedRef.current) {
              setConnectionState("closed");
              return;
            }
            setConnectionState("reconnecting");
            setTimeout(connect, RECONNECT_DELAY_MS);
          };

          ws.onerror = () => ws.close();
          return ws;
        })
        .catch(() => {
          // Ticket minting failed (backend down / auth rejected): retry loop.
          if (!stoppedRef.current) {
            setConnectionState("reconnecting");
            setTimeout(connect, RECONNECT_DELAY_MS);
          }
        });
    }

    connect();
    return () => {
      stoppedRef.current = true;
      socketRef.current?.close();
    };
  }, [projectId, token]);

  return { events, connectionState };
}
