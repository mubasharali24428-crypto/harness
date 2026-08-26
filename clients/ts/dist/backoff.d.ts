/**
 * H-012 exponential reconnect/request backoff with full jitter.
 *
 * EXTRACTION NOTE (mission M19-N5 / M20-T5 law: reuse, don't duplicate):
 * verbatim extraction of section7/src/lib/backoff.ts — BACKOFF_BASE_DELAY_MS
 * (1000), BACKOFF_MAX_DELAY_MS (30_000), `exponentialDelay` (nominal doubling
 * capped at 30s) and `backoffDelay` (scheduled wait drawn uniformly from
 * [0, nominal] — full jitter, so reconnecting clients don't stampede shared
 * endpoints after an outage). The Python SDK mirrors the same constants in
 * harness_sdk/runtime.py (`backoff_ceiling` / `backoff_delay`).
 */
export declare const BACKOFF_BASE_DELAY_MS = 1000;
export declare const BACKOFF_MAX_DELAY_MS = 30000;
/** Deterministic component: delay ceiling for `attempt` (1-indexed count of
 *  consecutive failures). Sequence: 1s, 2s, 4s … capped at 30s. */
export declare function exponentialDelay(attempt: number, baseMs?: number, capMs?: number): number;
/** Actual scheduled wait for `attempt`: uniformly jittered in [0, ceiling]. */
export declare function backoffDelay(attempt: number, baseMs?: number, capMs?: number): number;
declare const sleep: (ms: number) => Promise<unknown>;
export { sleep };
