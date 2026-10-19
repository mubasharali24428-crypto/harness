// Part 46 client: the collaboration socket as a framework-free controller (docs/realtime.md section 5).
// One cursor per run, dedupe and reordering through FrameReducer, backfill on a gap, reconnect with
// backoff and jitter, a polling fallback on collab-history, visible connection status. Everything
// outside the process (socket, ticket, history, clock, timers) is injected so the node-env tests can
// drive it with fake timers. collabApi.openCollabSocket (wake-up only) is kept for its own tests.
import { ApiError } from "@/lib/api";
import {
  CLIENT_SCHEMA_VERSION, type ConnStatus, type Frame, FrameReducer, HISTORY_PAGE, closePolicy,
  normalizeHistory, parseFrame, pollIntervalMs, reconnectDelayMs,
} from "./realtimeCore";

export interface SocketLike {
  close(): void;
  onopen: (() => void) | null;
  onmessage: ((e: { data: unknown }) => void) | null;
  onerror: (() => void) | null;
  onclose: ((e: { code?: number }) => void) | null;
}

export interface StreamDeps {
  mintTicket: () => Promise<{ ticket: string; fingerprint?: string }>;
  openSocket: (url: string) => SocketLike;
  fetchHistory: (after: number, limit: number) => Promise<unknown>;
  /** `ws://host` and the collab path, without the query string. */
  socketBase: string;
  socketPath: string;
  /** False when the browser has no WebSocket: the controller polls only. */
  hasSocket?: boolean;
  now?: () => number;
  random?: () => number;
  timers?: { set: (fn: () => void, ms: number) => unknown; clear: (h: unknown) => void };
  online?: boolean;
  hidden?: boolean;
}

export interface StreamSnapshot { status: ConnStatus; updateRequired: boolean; cursor: number; duplicates: number; pending: number }
export interface StreamHandlers {
  /** Frames in order, each exactly once per controller. */
  onEvents: (frames: Frame[]) => void;
  /** The server's log no longer reaches our cursor: drop what is shown, history reloads from 0. */
  onReset: () => void;
  onState: (s: StreamSnapshot) => void;
}

const QUIET_MS = 1000; // against a server without `ready`: live after one quiet second (5.3)
const STABLE_MS = 30_000; // reset the backoff only after a socket stayed live this long
const STRIKE_WINDOW_MS = 300_000;
const MAX_PAGES_PER_DRAIN = 50;

export function createCollabStream(deps: StreamDeps, h: StreamHandlers) {
  const now = deps.now ?? Date.now;
  const random = deps.random ?? Math.random;
  const timers = deps.timers ?? { set: (fn, ms) => setTimeout(fn, ms), clear: (t) => clearTimeout(t as ReturnType<typeof setTimeout>) };
  const reducer = new FrameReducer(0);
  let status: ConnStatus = "connecting";
  let updateRequired = false;
  let online = deps.online ?? true;
  let hidden = deps.hidden ?? false;
  let stopped = false;
  let attempt = 0;
  let gen = 0; // identity of the current socket; callbacks of an older one are ignored
  let ws: SocketLike | null = null;
  let retryAfterSeconds: number | null = null; // from the last RATE_LIMITED frame
  let slowCloses: number[] = [];
  let authCloses = 0;
  let reconnectT: unknown = null;
  let pollT: unknown = null;
  let quietT: unknown = null;
  let gapT: unknown = null;
  let stableT: unknown = null;
  let draining: Promise<void> | null = null;
  let again = false;
  let pollHoldUntil = 0; // a 429 on history: do not poll before this

  const snapshot = (): StreamSnapshot => ({ status, updateRequired, cursor: reducer.cursor, duplicates: reducer.duplicates, pending: reducer.pending() });
  const emit = () => h.onState(snapshot());
  const setStatus = (s: ConnStatus) => { if (s !== status) { status = s; emit(); } };
  const clear = (t: unknown) => { if (t !== null) timers.clear(t); };
  const deliver = (frames: Frame[]) => {
    if (frames.length === 0) return;
    if (!updateRequired && frames.some((f) => (f.schemaVersion ?? 1) > CLIENT_SCHEMA_VERSION)) { updateRequired = true; emit(); }
    h.onEvents(frames);
  };

  // ---- polling fallback / safety poll ----
  function schedulePoll(delay: number) {
    clear(pollT);
    if (stopped || status === "access_lost") return;
    pollT = timers.set(() => {
      pollT = null;
      void pollNow().then(() => schedulePoll(pollIntervalMs(status === "live", hidden, random)));
    }, delay);
  }

  function handlePollError(e: unknown) {
    if (e instanceof ApiError) {
      if (e.status === 401 || e.status === 403 || e.status === 404) return stopWith("access_lost");
      if (e.status === 429) {
        const ra = (e as ApiError & { retryAfter?: number }).retryAfter;
        pollHoldUntil = now() + (ra ?? 30) * 1000; // never retry faster than Retry-After
      }
    }
    // Any other failure: keep what we have, the next tick tries again.
  }

  async function drain() {
    for (let page = 0; page < MAX_PAGES_PER_DRAIN; page++) {
      if (stopped || !online || now() < pollHoldUntil) return;
      const before = reducer.cursor;
      let res;
      try { res = normalizeHistory(await deps.fetchHistory(reducer.cursor, HISTORY_PAGE), HISTORY_PAGE); } catch (e) { return handlePollError(e); }
      if (stopped) return;
      for (const f of res.events) deliver(reducer.push(f, now()));
      if (!res.hasMore || res.events.length === 0 || reducer.cursor === before) return;
    }
  }

  /** Page collab-history from the cursor through the same reducer. One at a time; a request that
   *  arrives while one runs makes it run once more. */
  function pollNow(): Promise<void> {
    if (draining) { again = true; return draining; }
    draining = (async () => {
      try { do { again = false; await drain(); } while (again && !stopped); } finally { draining = null; }
    })();
    return draining;
  }

  function armGap() {
    if (reducer.pending() === 0) return;
    if (reducer.gap(now())) { void pollNow(); return; }
    if (gapT !== null) return;
    gapT = timers.set(() => { gapT = null; if (reducer.gap(now())) void pollNow(); else armGap(); }, 3000);
  }

  // ---- socket lifecycle ----
  function stopWith(s: "access_lost") {
    clear(reconnectT); clear(pollT); clear(quietT); clear(gapT); clear(stableT);
    reconnectT = pollT = quietT = gapT = stableT = null;
    gen++;
    ws?.close();
    ws = null;
    stopped = true;
    setStatus(s);
  }

  function scheduleReconnect(floorMs: number) {
    clear(reconnectT);
    if (stopped || !online) return;
    const delay = reconnectDelayMs(attempt, random, floorMs);
    attempt += 1;
    reconnectT = timers.set(() => { reconnectT = null; void connect(); }, delay);
  }

  function lost(code: number | undefined) {
    clear(quietT); clear(gapT); clear(stableT);
    quietT = gapT = stableT = null;
    if (stopped) return;
    const policy = closePolicy(code ?? 1006, retryAfterSeconds);
    retryAfterSeconds = null;
    if (policy.action === "stop") return stopWith(policy.status);
    let floor = policy.floorMs;
    let next: ConnStatus = "reconnecting";
    if (policy.action === "wait") next = "polling";
    if (policy.action === "retry" && policy.strike === "slow") {
      const t = now();
      slowCloses = [...slowCloses.filter((x) => t - x < STRIKE_WINDOW_MS), t];
      if (slowCloses.length >= 3) { next = "polling"; floor = STRIKE_WINDOW_MS; slowCloses = []; }
    }
    if (policy.action === "retry" && policy.strike === "auth") {
      authCloses += 1;
      if (authCloses >= 2) { next = "polling"; floor = STRIKE_WINDOW_MS; authCloses = 0; } // mint once, never loop
    }
    setStatus(online ? next : "offline");
    schedulePoll(0); // polling starts immediately at the first close or failed connect
    scheduleReconnect(floor);
  }

  function goLive() {
    clear(quietT); quietT = null;
    if (status === "live") return;
    setStatus("live");
    authCloses = 0;
    clear(stableT);
    stableT = timers.set(() => { attempt = 0; stableT = null; }, STABLE_MS);
    schedulePoll(pollIntervalMs(true, hidden, random)); // keep only the 20 s safety poll
  }

  function armQuiet() {
    clear(quietT);
    quietT = timers.set(() => { quietT = null; if (status === "catching_up") goLive(); }, QUIET_MS);
  }

  async function connect() {
    if (stopped || !online) return;
    if (status !== "reconnecting" && status !== "polling") setStatus("connecting");
    let t;
    try { t = await deps.mintTicket(); } catch (e) {
      if (e instanceof ApiError && (e.status === 401 || e.status === 404)) return stopWith("access_lost");
      if (e instanceof ApiError && e.status === 429) {
        const ra = (e as ApiError & { retryAfter?: number }).retryAfter;
        setStatus("polling"); schedulePoll(0); return scheduleReconnect((ra ?? 30) * 1000);
      }
      setStatus("reconnecting"); schedulePoll(0); return scheduleReconnect(0);
    }
    if (stopped || !online) return;
    const q = new URLSearchParams({
      ticket: t.ticket, afterSeq: String(reducer.cursor), schemaVersion: String(CLIENT_SCHEMA_VERSION), control: "1",
    });
    if (t.fingerprint) q.set("fingerprint", t.fingerprint);
    const mine = ++gen;
    let sock: SocketLike;
    try { sock = deps.openSocket(`${deps.socketBase}${deps.socketPath}?${q}`); } catch { return lost(1006); }
    ws = sock;
    sock.onopen = () => { if (mine === gen) { setStatus("catching_up"); armQuiet(); } };
    sock.onmessage = (e) => {
      if (mine !== gen) return;
      const p = parseFrame(e.data);
      if (status === "catching_up") armQuiet();
      if (p.type === "event") { deliver(reducer.push(p.frame, now())); armGap(); }
      else if (p.type === "truncated") void pollNow();
      else if (p.type === "error") retryAfterSeconds = p.retryAfterSeconds;
      else if (p.type === "ready") {
        if (p.schemaVersion > CLIENT_SCHEMA_VERSION && !updateRequired) { updateRequired = true; emit(); }
        if (p.cursorAhead) { reducer.reset(0); h.onReset(); void pollNow(); }
        else if (p.headSeq > reducer.cursor && reducer.pending() === 0) void pollNow();
        goLive();
      }
    };
    sock.onerror = () => undefined; // onclose always follows
    sock.onclose = (e) => { if (mine === gen) { ws = null; lost(e?.code); } };
  }

  return {
    start() {
      stopped = false;
      if (!online) { setStatus("offline"); return; }
      if (deps.hasSocket === false) { setStatus("polling"); schedulePoll(0); return; }
      emit(); // the socket's catch-up loads history; polling starts at the first failure (5.3)
      void connect();
    },
    stop() {
      stopped = true;
      gen++;
      for (const t of [reconnectT, pollT, quietT, gapT, stableT]) clear(t);
      reconnectT = pollT = quietT = gapT = stableT = null;
      ws?.close();
      ws = null;
    },
    setOnline(v: boolean) {
      if (v === online || stopped) return;
      online = v;
      if (!v) {
        gen++; ws?.close(); ws = null;
        for (const t of [reconnectT, pollT, quietT, gapT, stableT]) clear(t);
        reconnectT = pollT = quietT = gapT = stableT = null;
        setStatus("offline");
        return;
      }
      setStatus("connecting"); // back online: retry on the online event, not on the backoff timer
      schedulePoll(0);
      if (deps.hasSocket !== false) void connect(); else setStatus("polling");
    },
    /** Tab visibility: a hidden tab polls slowly, a visible one resumes at once (5.3). */
    setHidden(v: boolean) {
      hidden = v;
      if (!v && !stopped && online && status !== "access_lost") schedulePoll(0);
    },
    pollNow,
    snapshot,
  };
}
