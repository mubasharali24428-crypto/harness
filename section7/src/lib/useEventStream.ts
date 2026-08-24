"use client";

import { useEffect, useRef, useState } from "react";
import type { EventEnvelope } from "@contracts/events";
import { fetchWsTicket, IDENTITY_WS_BASE } from "./api";
import { backoffDelay, exponentialDelay, BACKOFF_MAX_DELAY_MS } from "./backoff";

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

/** H-012: WS connection lifecycle exposed to the UI.
 *
 *  - `attempt` is the consecutive-failure count since the last successful open
 *    (0 while connected/connecting, ≥1 once a reconnect has been scheduled).
 *  - `nextDelayMs` is the ceiling of the currently scheduled reconnect wait
 *    (0 when no reconnect is pending). */
export interface EventStreamStatus {
  state: ConnectionState;
  attempt: number;
  nextDelayMs: number;
}

// ---------------------------------------------------------------------------
// Headless controller — owns the full connect/reconnect lifecycle against an
// injectable socket factory, so tests can drive a mock WS lifecycle in a plain
// node environment. The React hook below is a thin adapter over it.
// ---------------------------------------------------------------------------

export interface SocketLike {
  close(): void;
  onopen: (() => void) | null;
  onmessage: ((evt: { data: unknown }) => void) | null;
  onerror: (() => void) | null;
  onclose: (() => void) | null;
}

export type SocketFactory = (url: string) => SocketLike;
export type TimerScheduler = (fn: () => void, delayMs: number) => () => void;

export interface ControllerOptions {
  /** Test seam: overrides single-use ticket minting entirely when provided. */
  mintTicket?: () => Promise<string>;
  socketFactory?: SocketFactory;
  schedule?: TimerScheduler;
  /** Called with each parsed envelope; errors here must never kill the stream. */
  onEvent?: (envelope: EventEnvelope) => void;
  onStateChange?: (status: EventStreamStatus) => void;
}

const defaultSchedule: TimerScheduler = (fn, delayMs) => {
  const t = setTimeout(fn, delayMs);
  return () => clearTimeout(t);
};

const noop = () => {};

export class EventStreamController {
  private readonly mintTicketOverride?: () => Promise<string>;
  private readonly makeSocket: SocketFactory;
  private readonly schedule: TimerScheduler;
  private readonly onEvent: (envelope: EventEnvelope) => void;
  private readonly onStateChange: (status: EventStreamStatus) => void;

  private status: EventStreamStatus = { state: "connecting", attempt: 0, nextDelayMs: 0 };
  private stopped = false;
  /** Bumps on every state transition; callbacks from stale sockets/timers are ignored. */
  private generation = 0;
  private socket: SocketLike | null = null;
  private cancelTimer: (() => void) | null = null;
  private lastSeq = 0;
  private projectId = "";
  private token = "";

  constructor(options: ControllerOptions = {}) {
    this.mintTicketOverride = options.mintTicket;
    this.makeSocket =
      options.socketFactory ?? ((url) => new WebSocket(url) as unknown as SocketLike);
    this.schedule = options.schedule ?? defaultSchedule;
    this.onEvent = options.onEvent ?? noop;
    this.onStateChange = options.onStateChange ?? noop;
  }

  getStatus(): EventStreamStatus {
    return { ...this.status };
  }

  private setStatus(patch: Partial<EventStreamStatus>): void {
    this.status = { ...this.status, ...patch };
    this.onStateChange(this.getStatus());
  }

  /** Highest event seq seen — passed back to the server on every reconnect so
   *  its replay resumes exactly where we left off. */
  getLastSeq(): number {
    return this.lastSeq;
  }

  start(projectId: string, token: string): void {
    this.projectId = projectId;
    this.token = token;
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.generation += 1;
    this.cancelPendingTimer();
    this.socket?.close();
    this.socket = null;
    this.setStatus({ state: "closed", nextDelayMs: 0 });
  }

  /** Test seam: simulate the transport dropping mid-flight. */
  forceDrop(): void {
    this.handleClose(this.generation);
  }

  /** H8: tickets are single-use, so EVERY connect attempt mints a fresh one;
   *  the long-lived bearer token never appears in a URL. */
  private mintTicket(): Promise<string> {
    if (this.mintTicketOverride) return this.mintTicketOverride();
    if (!this.projectId || !this.token) {
      return Promise.reject(new Error("useEventStream: no session to mint a WS ticket"));
    }
    return fetchWsTicket(this.projectId, this.token);
  }

  private connect(): void {
    if (this.stopped) return;
    const gen = ++this.generation;
    this.cancelPendingTimer();
    this.setStatus({
      state: this.status.attempt > 0 ? "reconnecting" : "connecting",
      nextDelayMs: 0,
    });

    this.mintTicket()
      .then((ticket) => {
        if (this.stopped || gen !== this.generation) return;
        const ws = this.makeSocket(this.buildUrl(ticket));
        this.socket = ws;

        ws.onopen = () => {
          if (gen !== this.generation) return;
          // A successful open clears the consecutive-failure count, so the
          // next drop starts the backoff sequence over from the base delay.
          this.setStatus({ state: "open", attempt: 0 });
        };

        ws.onmessage = (evt) => {
          if (gen !== this.generation) return;
          this.handleMessage(evt.data);
        };

        ws.onerror = () => {
          if (gen !== this.generation) return;
          try {
            ws.close();
          } catch {
            /* already closing */
          }
        };

        ws.onclose = () => {
          if (gen !== this.generation) return;
          this.handleClose(gen);
        };
      })
      .catch(() => {
        // Ticket minting failed (backend down / auth rejected / no session).
        if (this.stopped || gen !== this.generation) return;
        this.scheduleReconnect();
      });
  }

  private buildUrl(ticket: string): string {
    // Single-use tickets are short-lived secrets — always URL-encode them and
    // never log or persist the resulting URL.
    const pid = encodeURIComponent(this.projectId);
    return `${IDENTITY_WS_BASE}/api/v1/ws/${pid}?afterSeq=${this.lastSeq}&ticket=${encodeURIComponent(
      ticket,
    )}`;
  }

  private cancelPendingTimer(): void {
    if (this.cancelTimer) {
      this.cancelTimer();
      this.cancelTimer = null;
    }
  }

  private handleClose(gen: number): void {
    if (this.stopped || gen !== this.generation) return;
    this.socket = null;
    this.scheduleReconnect();
  }

  /** H-012: exponential backoff with full jitter (base 1s, cap 30s), replacing
   *  the old fixed-1.5s retry loop. Attempt count is surfaced to the UI. */
  private scheduleReconnect(): void {
    if (this.stopped) return;
    const attempt = this.status.attempt + 1;
    const ceiling = exponentialDelay(attempt);
    const jittered = Math.min(BACKOFF_MAX_DELAY_MS, backoffDelay(attempt));
    this.setStatus({ state: "reconnecting", attempt, nextDelayMs: ceiling });

    const genAtSchedule = ++this.generation;
    this.cancelPendingTimer();
    let cancelled = false;
    this.cancelTimer = () => {
      cancelled = true;
    };
    const fire = () => {
      if (cancelled || this.stopped || genAtSchedule !== this.generation) return;
      this.cancelTimer = null;
      this.connect();
    };
    void this.schedule(fire, jittered);
  }

  private handleMessage(raw: unknown): void {
    // H-012: guarded JSON.parse — a malformed frame is logged and dropped,
    // never allowed to throw out of the message handler.
    let parsed: unknown;
    try {
      parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
    } catch (err) {
      console.error("[useEventStream] malformed WS frame dropped:", err instanceof Error ? err.message : err);
      return;
    }
    if (typeof parsed !== "object" || parsed === null) {
      console.error("[useEventStream] malformed WS frame dropped: frame is not an object");
      return;
    }
    const candidate = parsed as Partial<EventEnvelope>;
    if (typeof candidate.seq !== "number" || !Number.isFinite(candidate.seq)) {
      console.error("[useEventStream] malformed WS frame dropped: missing numeric seq");
      return;
    }
    const envelope = parsed as EventEnvelope;
    this.lastSeq = Math.max(this.lastSeq, envelope.seq);
    try {
      this.onEvent(envelope);
    } catch (err) {
      // A consumer bug must not tear down the socket either.
      console.error("[useEventStream] onEvent handler threw:", err instanceof Error ? err.message : err);
    }
  }
}

// ---------------------------------------------------------------------------
// React adapter
// ---------------------------------------------------------------------------

/** Connects to Section 1's per-project event bus WS. The server itself replays every
 *  missed event (by `afterSeq`) before switching to live push on the SAME socket, so a
 *  reconnect just needs to reopen with the highest seq we've already seen — no separate
 *  REST replay call needed for the steady-state reconnect path.
 *
 *  H-012: reconnection uses exponential backoff with jitter (base 1s, cap 30s)
 *  instead of a fixed 1.5s loop; `attempt` exposes the consecutive failure count
 *  so UI can show e.g. "Reconnecting (attempt 3)". */
export function useEventStream(projectId: string | null, token: string | null) {
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [attempt, setAttempt] = useState(0);
  const controllerRef = useRef<EventStreamController | null>(null);

  useEffect(() => {
    if (!projectId || !token) {
      setConnectionState("connecting");
      setAttempt(0);
      return;
    }

    const controller = new EventStreamController({
      onEvent: (envelope) => setEvents((prev) => mergeEvents(prev, envelope)),
      onStateChange: (status) => {
        setConnectionState(status.state);
        setAttempt(status.attempt);
      },
    });
    controllerRef.current = controller;
    controller.start(projectId, token);

    return () => {
      controller.stop();
      controllerRef.current = null;
    };
  }, [projectId, token]);

  return { events, connectionState, attempt };
}
