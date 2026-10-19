// Part 46 client rules as pure code (docs/realtime.md section 5). No React, no timers, no fetch: the
// stream controller (collabStream.ts) feeds this module and the tests pin it. It mirrors the reference
// reducer section4/src/harness_orchestrator/delivery.py `Reducer` rule for rule; the same cases as
// section4/tests/unit/test_delivery_reducer.py are replayed in realtimeCore.test.ts.
import { ApiError } from "@/lib/api";
import { exponentialDelay } from "@/lib/backoff";

// ---- frames -------------------------------------------------------------------------------------

/** One durable collaboration event as the socket and collab-history deliver it. Everything but
 *  seq/kind/actor/payload/ts is optional: the history route may still answer the older shape. */
export interface Frame {
  seq: number;
  prevSeq?: number | null;
  actor: string;
  kind: string;
  payload: Record<string, unknown>;
  ts: string;
  schemaVersion?: number;
  runId?: string;
}

/** The newest frame schema this client understands; sent as `schemaVersion=` on the socket. */
export const CLIENT_SCHEMA_VERSION = 1;
export const REORDER_WINDOW = 256;
export const REORDER_MAX_WAIT_MS = 3000;
export const HISTORY_PAGE = 200;

export type Parsed =
  | { type: "event"; frame: Frame }
  | { type: "truncated"; oldestSeqSent: number; afterSeq: number }
  | { type: "ready"; afterSeq: number; headSeq: number; cursorAhead: boolean; schemaVersion: number }
  | { type: "error"; code: string; retryAfterSeconds: number | null }
  | { type: "unknown" };

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const int = (v: unknown): number | null => (typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null);

/** Never throws. Unknown frame types and unknown control values are ignored (`unknown`), per 5.2. */
export function parseFrame(raw: unknown): Parsed {
  let v = raw;
  if (typeof v === "string") {
    try { v = JSON.parse(v); } catch { return { type: "unknown" }; }
  }
  if (!isObj(v)) return { type: "unknown" };
  if (v.replayTruncated === true) {
    return { type: "truncated", oldestSeqSent: int(v.oldestSeqSent) ?? 0, afterSeq: int(v.afterSeq) ?? 0 };
  }
  if (v.control === "ready") {
    return {
      type: "ready", afterSeq: int(v.afterSeq) ?? 0, headSeq: int(v.headSeq) ?? 0,
      cursorAhead: v.cursorAhead === true, schemaVersion: int(v.schemaVersion) ?? 1,
    };
  }
  if (isObj(v.error)) {
    const ra = typeof v.retryAfterSeconds === "number" && v.retryAfterSeconds > 0 ? v.retryAfterSeconds : null;
    return { type: "error", code: String(v.error.code ?? "UNKNOWN"), retryAfterSeconds: ra };
  }
  if (typeof v.control === "string") return { type: "unknown" };
  return normalizeEvent(v);
}

/** One history row or socket event frame -> Frame, or `unknown` when it has no usable seq/kind. */
export function normalizeEvent(v: Record<string, unknown>): Parsed {
  const seq = int(v.seq);
  if (seq === null || typeof v.kind !== "string") return { type: "unknown" };
  const prev = v.prevSeq === null || v.prevSeq === undefined ? null : int(v.prevSeq);
  return {
    type: "event",
    frame: {
      seq, prevSeq: prev, kind: v.kind, actor: typeof v.actor === "string" ? v.actor : "",
      payload: isObj(v.payload) ? v.payload : {}, ts: typeof v.ts === "string" ? v.ts : "",
      schemaVersion: int(v.schemaVersion) ?? 1, runId: typeof v.runId === "string" ? v.runId : undefined,
    },
  };
}

export interface HistoryPage { events: Frame[]; hasMore: boolean; nextCursor: number | null; headSeq: number | null }

/** Accepts the enriched page and the older `{events}` shape. Absent `hasMore` means "more may exist
 *  when events.length >= limit" (5.2); absent `prevSeq` means "ordered by cursor only". */
export function normalizeHistory(body: unknown, limit: number): HistoryPage {
  const rows = isObj(body) && Array.isArray(body.events) ? body.events : [];
  const events: Frame[] = [];
  for (const r of rows) {
    const p = isObj(r) ? normalizeEvent(r) : ({ type: "unknown" } as Parsed);
    if (p.type === "event") events.push(p.frame);
  }
  const b = isObj(body) ? body : {};
  return {
    events,
    hasMore: typeof b.hasMore === "boolean" ? b.hasMore : events.length >= limit,
    nextCursor: int(b.nextCursor), headSeq: int(b.headSeq),
  };
}

// ---- the reducer --------------------------------------------------------------------------------

/** One run's cursor, dedupe by (runId, seq) (the run is implicit: one reducer per run) and the
 *  out-of-order buffer. `push` returns the frames that are now deliverable, in order. */
export class FrameReducer {
  cursor: number;
  duplicates = 0;
  private held = new Map<number, { frame: Frame; at: number }>();

  constructor(cursor = 0, private readonly window = REORDER_WINDOW, private readonly maxWaitMs = REORDER_MAX_WAIT_MS) {
    this.cursor = cursor;
  }

  push(frame: Frame, nowMs = 0): Frame[] {
    if (frame.seq <= this.cursor || this.held.has(frame.seq)) {
      this.duplicates += 1;
      return [];
    }
    const out: Frame[] = [];
    if (this.ready(frame)) this.deliver(frame, out);
    else this.held.set(frame.seq, { frame, at: nowMs });
    for (let progressed = true; progressed;) { // a delivered frame may unlock buffered successors
      progressed = false;
      for (const seq of [...this.held.keys()].sort((a, b) => a - b)) {
        if (seq <= this.cursor) { this.held.delete(seq); continue; } // superseded while waiting
        const h = this.held.get(seq)!;
        if (this.ready(h.frame)) {
          this.held.delete(seq);
          this.deliver(h.frame, out);
          progressed = true;
          break;
        }
      }
    }
    return out;
  }

  /** True when the buffer is full or its oldest frame has waited maxWaitMs: backfill from `cursor`. */
  gap(nowMs = 0): boolean {
    if (this.held.size === 0) return false;
    const oldest = Math.min(...[...this.held.values()].map((h) => h.at));
    return this.held.size >= this.window || nowMs - oldest >= this.maxWaitMs;
  }

  pending(): number { return this.held.size; }

  /** `ready.cursorAhead`: the log under the cursor is gone. */
  reset(cursor = 0): void { this.cursor = cursor; this.held.clear(); }

  private ready(f: Frame): boolean { return f.prevSeq === null || f.prevSeq === undefined || f.prevSeq <= this.cursor; }
  private deliver(f: Frame, out: Frame[]): void { this.cursor = f.seq; out.push(f); }
}

// ---- backoff and close codes (5.3) --------------------------------------------------------------

/** Attempt n (0-based): d = min(30 s, 1 s * 2^n), delay = d/2 + random(0, d/2). `floorMs` (a close
 *  code's own minimum) wins over a shorter delay. */
export function reconnectDelayMs(attempt: number, random: () => number = Math.random, floorMs = 0): number {
  const d = exponentialDelay(attempt + 1);
  return Math.max(Math.floor(d / 2 + random() * (d / 2)), floorMs);
}

/** Polling interval: 3 s +-20% jitter when the socket is not live, 20 s safety poll when it is,
 *  stretched to 30 s in a hidden tab. */
export function pollIntervalMs(live: boolean, hidden: boolean, random: () => number = Math.random): number {
  if (hidden) return 30_000;
  if (live) return 20_000;
  return Math.floor(3000 * (0.8 + random() * 0.4));
}

export type CloseAction =
  | { action: "retry"; floorMs: number; strike?: "slow" | "auth" }
  | { action: "stop"; status: "access_lost" }
  | { action: "wait"; floorMs: number };

/** docs/realtime.md section 4 "Stable close codes" -> what the client does. */
export function closePolicy(code: number, retryAfterSeconds: number | null = null): CloseAction {
  switch (code) {
    case 4404: case 4410: return { action: "stop", status: "access_lost" };
    case 4429: return { action: "wait", floorMs: (retryAfterSeconds ?? 30) * 1000 };
    case 1013: return { action: "retry", floorMs: 5000 };
    case 4408: return { action: "retry", floorMs: 5000, strike: "slow" };
    case 4401: case 4403: return { action: "retry", floorMs: 0, strike: "auth" };
    default: return { action: "retry", floorMs: 0 }; // 1000, 1001, 1006 and anything unknown
  }
}

// ---- status (5.3) -------------------------------------------------------------------------------

export type ConnStatus =
  | "connecting" | "catching_up" | "live" | "reconnecting" | "polling" | "offline" | "access_lost";

/** Text, never colour alone. `update_required` is a flag on top of the status, not a status. */
export const STATUS_TEXT: Record<ConnStatus, string> = {
  connecting: "Connecting",
  catching_up: "Catching up",
  live: "Live",
  reconnecting: "Reconnecting, refreshing every 3 s",
  polling: "Live updates paused, refreshing every 3 s",
  offline: "You are offline",
  access_lost: "You no longer have access to this run",
};
export const UPDATE_REQUIRED_TEXT = "This page is out of date, reload to see all activity";

export function statusText(status: ConnStatus, updateRequired: boolean): string {
  return updateRequired ? `${STATUS_TEXT[status]}. ${UPDATE_REQUIRED_TEXT}` : STATUS_TEXT[status];
}

/** What to say when the status changes, or null. Per 5.3: only `reconnecting` after 5 s without a
 *  socket, `live` after a reconnecting/polling spell, `access_lost`, `update_required`. Never each retry. */
export function createStatusAnnouncer(
  announce: (text: string, urgent?: boolean) => void,
  timers: { set: (fn: () => void, ms: number) => unknown; clear: (h: unknown) => void } = {
    set: (fn, ms) => setTimeout(fn, ms), clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
  },
) {
  let wasDown = false;
  let pending: unknown = null;
  let said = false;
  const cancel = () => { if (pending !== null) { timers.clear(pending); pending = null; } };
  return {
    onStatus(status: ConnStatus) {
      if (status === "reconnecting" || status === "polling" || status === "offline") {
        if (pending === null && !said) {
          pending = timers.set(() => {
            pending = null; said = true; wasDown = true;
            announce("Live updates are paused. The page keeps refreshing.");
          }, 5000);
        }
        return;
      }
      cancel();
      if (status === "live") {
        if (wasDown) announce("Live updates are back.");
        wasDown = false; said = false;
      } else if (status === "access_lost") {
        announce(STATUS_TEXT.access_lost, true);
      }
    },
    onUpdateRequired() { announce(UPDATE_REQUIRED_TEXT); },
    dispose: cancel,
  };
}

// ---- stale state on mutations (5.4) -------------------------------------------------------------

/** 409 codes that mean "my view is stale": re-read, show what changed, never auto-resubmit. */
export const STALE_CODES = new Set(["STALE_CLIENT_STATE", "STALE_INTENT_VERSION", "INTENT_CONFLICT", "DIGEST_MISMATCH", "STALE_COMMENT_VERSION", "VERSION_CONFLICT"]);
export const isStale = (e: unknown): boolean => e instanceof ApiError && e.status === 409 && STALE_CODES.has(e.code);

/** Seconds a mutation control stays disabled after a 429 (the caller read Retry-After when it could). */
export function cooldownSeconds(e: unknown): number | null {
  if (!(e instanceof ApiError) || e.status !== 429) return null;
  const ra = (e as ApiError & { retryAfter?: number }).retryAfter;
  return typeof ra === "number" && ra > 0 ? Math.min(ra, 300) : 30;
}

export const STALE_TEXT = "This changed while you were looking. The latest version is now shown; review it and try again.";

/** Plain-language text for a failed mutation on the collaboration UI. */
export function mutationError(e: unknown, fallback: (e: unknown) => string): string {
  if (isStale(e)) return STALE_TEXT;
  const wait = cooldownSeconds(e);
  if (wait !== null) return `You are doing that too often. Wait ${wait} seconds before trying again.`;
  return fallback(e);
}
