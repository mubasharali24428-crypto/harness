/** H-012: exponential reconnect backoff with jitter.
 *
 *  Replaces the old fixed RECONNECT_DELAY_MS = 1500 retry loop. The nominal
 *  delay doubles every failed attempt starting from BACKOFF_BASE_DELAY_MS and
 *  is capped at BACKOFF_MAX_DELAY_MS; full jitter ([0, nominal]) spreads
 *  reconnecting clients out so they don't stampede the single-use ticket
 *  endpoint after a shared outage. */

export const BACKOFF_BASE_DELAY_MS = 1000;
export const BACKOFF_MAX_DELAY_MS = 30_000;

/** Deterministic component: delay ceiling for reconnect `attempt` (1-indexed
 *  count of consecutive failures). Sequence: 1s, 2s, 4s … capped at 30s. */
export function exponentialDelay(attempt: number): number {
  if (attempt < 1) return 0;
  const raw = BACKOFF_BASE_DELAY_MS * 2 ** (attempt - 1);
  return Math.min(BACKOFF_MAX_DELAY_MS, raw);
}

/** Actual scheduled wait for `attempt`: uniformly jittered in [0, ceiling]. */
export function backoffDelay(attempt: number): number {
  return Math.floor(Math.random() * exponentialDelay(attempt));
}

// ---------------------------------------------------------------------------
// H-012: refetch coalescing — event-triggered REST refreshes are debounced so
// a burst of bus events (e.g. a reconnect replay) produces ONE refetch per
// window instead of one per event.
// ---------------------------------------------------------------------------

export const REFETCH_COALESCE_MS = 2000;

function defaultCoalesceSchedule(fn: () => void, delayMs: number): () => void {
  const t = setTimeout(fn, delayMs);
  return () => clearTimeout(t);
}

export interface RefetchCoalescer {
  /** Record an event arrival. Resets the window; `fire` runs once, trailing,
   *  `windowMs` after the LAST push in a burst. */
  push(): void;
  /** Drop any pending fire (used on teardown / session change). */
  cancel(): void;
  pending(): boolean;
}

/** Framework-free so it is unit-testable without a DOM. */
export function createRefetchCoalescer(
  windowMs: number,
  fire: () => void,
  schedule: (fn: () => void, delayMs: number) => () => void = defaultCoalesceSchedule,
): RefetchCoalescer {
  let cancelTimer: (() => void) | null = null;

  return {
    push() {
      if (cancelTimer) cancelTimer();
      // Trailing-edge debounce: clear the slot as soon as the timer fires so
      // pending() never lies about a fire that already happened.
      cancelTimer = schedule(() => {
        cancelTimer = null;
        fire();
      }, windowMs);
    },
    cancel() {
      if (cancelTimer) {
        cancelTimer();
        cancelTimer = null;
      }
    },
    pending() {
      return cancelTimer !== null;
    },
  };
}
