"use client";

// Part 46: React adapter for collabStream.ts. Builds the real dependencies (ticket, WebSocket, history),
// wires online/offline/visibility, keeps handlers in refs so a re-render never reconnects, and turns
// status changes into the throttled announcements of docs/realtime.md 5.3.
import { useEffect, useRef, useState } from "react";
import { IDENTITY_BASE } from "@/lib/api";
import { call, post, root } from "./collabApi";
import { type SocketLike, type StreamSnapshot, createCollabStream } from "./collabStream";
import { type ConnStatus, type Frame, UPDATE_REQUIRED_TEXT, createStatusAnnouncer } from "./realtimeCore";

export interface StreamView { status: ConnStatus; updateRequired: boolean }

export function useCollabStream(a: {
  projectId?: string; runId: string; token: string | null; enabled: boolean;
  onEvents: (frames: Frame[]) => void; onReset: () => void;
  announce?: (text: string, urgent?: boolean) => void;
}): StreamView {
  const [view, setView] = useState<StreamView>({ status: "connecting", updateRequired: false });
  const h = useRef(a);
  h.current = a;
  const { projectId, runId, token, enabled } = a;

  useEffect(() => {
    if (!token || !enabled) return;
    const say = (t: string, urgent?: boolean) => h.current.announce?.(t, urgent);
    const speech = createStatusAnnouncer(say);
    const pid = encodeURIComponent(projectId ?? "");
    let wasUpdate = false;
    const stream = createCollabStream(
      {
        mintTicket: () => post<{ ticket: string; fingerprint?: string }>(`${IDENTITY_BASE}/api/v1/ws-ticket`, token, { projectId }),
        openSocket: (url) => new WebSocket(url) as unknown as SocketLike,
        fetchHistory: (after, limit) => call<unknown>(`${root(runId)}/collab-history?after=${after}&limit=${limit}`, token),
        socketBase: IDENTITY_BASE.replace(/^http/, "ws"),
        socketPath: `/api/v1/ws/${pid}/runs/${encodeURIComponent(runId)}/collab`,
        hasSocket: typeof WebSocket !== "undefined" && !!projectId,
        online: typeof navigator === "undefined" ? true : navigator.onLine !== false,
        hidden: typeof document !== "undefined" && document.visibilityState === "hidden",
      },
      {
        onEvents: (f) => h.current.onEvents(f),
        onReset: () => h.current.onReset(),
        onState: (s: StreamSnapshot) => {
          setView((p) => (p.status === s.status && p.updateRequired === s.updateRequired ? p : { status: s.status, updateRequired: s.updateRequired }));
          speech.onStatus(s.status);
          if (s.updateRequired && !wasUpdate) { wasUpdate = true; say(UPDATE_REQUIRED_TEXT); }
        },
      },
    );
    const up = () => stream.setOnline(true);
    const down = () => stream.setOnline(false);
    const vis = () => stream.setHidden(document.visibilityState === "hidden");
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    document.addEventListener("visibilitychange", vis);
    stream.start();
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
      document.removeEventListener("visibilitychange", vis);
      speech.dispose();
      stream.stop();
    };
  }, [projectId, runId, token, enabled]);

  return view;
}
