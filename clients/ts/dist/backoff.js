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
export const BACKOFF_BASE_DELAY_MS = 1000;
export const BACKOFF_MAX_DELAY_MS = 30_000;
/** Deterministic component: delay ceiling for `attempt` (1-indexed count of
 *  consecutive failures). Sequence: 1s, 2s, 4s … capped at 30s. */
export function exponentialDelay(attempt, baseMs = BACKOFF_BASE_DELAY_MS, capMs = BACKOFF_MAX_DELAY_MS) {
    if (attempt < 1)
        return 0;
    return Math.min(capMs, baseMs * 2 ** (attempt - 1));
}
/** Actual scheduled wait for `attempt`: uniformly jittered in [0, ceiling]. */
export function backoffDelay(attempt, baseMs = BACKOFF_BASE_DELAY_MS, capMs = BACKOFF_MAX_DELAY_MS) {
    const ceiling = exponentialDelay(attempt, baseMs, capMs);
    if (ceiling <= 0)
        return 0;
    return Math.floor(Math.random() * ceiling);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export { sleep };
