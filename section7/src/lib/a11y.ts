// Part 56 (collaboration UI) shared accessibility primitives, framework-free so vitest covers them with
// fake timers: the throttled announcer behind the one live region, and the reduced-motion check.
// docs/accessibility.md has the rules in prose.

export interface AnnouncerOptions {
  /** Called with the text for the polite region. */
  polite: (text: string) => void;
  /** Called with the text for the assertive region (urgent items only). */
  assertive: (text: string) => void;
  /** At most one polite message per window; messages inside it are coalesced. */
  minIntervalMs?: number;
  /** Urgent messages are immediate but never more often than this. */
  urgentIntervalMs?: number;
  /** Most distinct messages remembered inside a window. */
  maxQueued?: number;
  now?: () => number;
  timers?: { set: (fn: () => void, ms: number) => unknown; clear: (h: unknown) => void };
}

export interface Announcer {
  /** Queue a polite announcement, or (urgent) say it assertively now. Empty text is ignored. */
  announce(text: string, urgent?: boolean): void;
  dispose(): void;
}

export const POLITE_INTERVAL_MS = 4000;
export const URGENT_INTERVAL_MS = 1000;

/** First message goes out at once; the rest of the window is collected and sent as ONE message when
 *  it closes ("3 updates. Latest: ..."), so a burst never becomes a stream of speech. */
export function createAnnouncer(o: AnnouncerOptions): Announcer {
  const now = o.now ?? Date.now;
  const interval = o.minIntervalMs ?? POLITE_INTERVAL_MS;
  const urgentGap = o.urgentIntervalMs ?? URGENT_INTERVAL_MS;
  const cap = o.maxQueued ?? 20;
  const timers = o.timers ?? { set: (fn, ms) => setTimeout(fn, ms), clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>) };
  let lastPolite = -Infinity;
  let lastUrgent = -Infinity;
  let queue: string[] = [];
  let dropped = 0;
  let timer: unknown = null;
  let urgentPending: string | null = null;
  let urgentTimer: unknown = null;

  const flush = () => {
    timer = null;
    if (queue.length === 0) return;
    const items = queue;
    const extra = dropped;
    queue = []; dropped = 0;
    const total = items.length + extra;
    lastPolite = now();
    o.polite(total === 1 ? items[0] : `${total} updates. Latest: ${items[items.length - 1]}`);
    timer = timers.set(flush, interval); // the window that just opened holds the next burst
  };

  return {
    announce(text, urgent = false) {
      const t = text.trim();
      if (!t) return;
      if (urgent) {
        const wait = lastUrgent + urgentGap - now();
        if (wait <= 0) { lastUrgent = now(); o.assertive(t); return; }
        urgentPending = t; // inside the gap: keep only the newest
        if (urgentTimer === null) {
          urgentTimer = timers.set(() => {
            urgentTimer = null;
            if (urgentPending) { lastUrgent = now(); o.assertive(urgentPending); urgentPending = null; }
          }, wait);
        }
        return;
      }
      if (queue[queue.length - 1] === t) return; // the same sentence twice in a row is one
      if (queue.length >= cap) { dropped += 1; return; }
      queue.push(t);
      if (timer === null && now() - lastPolite >= interval) { flush(); return; }
      if (timer === null) timer = timers.set(flush, Math.max(0, lastPolite + interval - now()));
    },
    dispose() {
      if (timer !== null) timers.clear(timer);
      if (urgentTimer !== null) timers.clear(urgentTimer);
      timer = urgentTimer = null;
      queue = []; urgentPending = null;
    },
  };
}

/** `prefers-reduced-motion: reduce`. False where matchMedia does not exist (node, old browsers). */
export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** `behavior` for scrollIntoView / scrollTo: never smooth when the person asked for less motion. */
export const scrollBehavior = (): ScrollBehavior => (prefersReducedMotion() ? "auto" : "smooth");
