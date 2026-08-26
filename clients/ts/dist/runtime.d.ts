/**
 * Shared transport runtime for the Harness TypeScript SDK.
 *
 * Handwritten layer every generated client rides on (the twin of
 * clients/python/harness_sdk/runtime.py — both extracted from section7's
 * battle-tested browser client):
 *
 * - bearer-token auth header,
 * - frozen error-envelope parsing (errors.ts),
 * - retries with exponential backoff + full jitter for transient failures
 *   (429 anywhere; 502/503/504 + connection errors on idempotent methods;
 *   Retry-After honored),
 * - unset optional query params dropped (None-sentinel semantics shared with
 *   the Python runtime).
 */
/** Extraction of section7/src/lib/backoff.ts constants (H-012). */
export declare const BACKOFF_BASE_MS = 1000;
export declare const BACKOFF_CAP_MS = 30000;
export declare const SDK_USER_AGENT = "harness-ts-sdk/0.1.0";
/** Section service name -> default local port (deploy/docker-compose.yml). */
export declare const SERVICE_PORTS: Record<string, number>;
/** Default base URL for a section: HARNESS_<NAME>_URL env var, else
 *  `http://localhost:<default port>`. (Node-only env read; browsers pass
 *  explicit base URLs via Harness options instead.) */
export declare function baseUrlFor(service: string): string;
export interface HarnessHttpOptions {
    token?: string | null;
    /** Per-request timeout in milliseconds (default 15000). */
    timeoutMs?: number;
    maxRetries?: number;
    backoffBaseMs?: number;
    backoffCapMs?: number;
    /**
     * Test seam: replace the global fetch (node:test / vitest mocks).
     * Signature mirrors the WHATWG fetch initializer.
     */
    fetchImpl?: FetchLike;
    sleepFn?: (ms: number) => Promise<void>;
}
export interface RequestInitLite {
    params?: Record<string, unknown> | null;
    body?: unknown;
    auth?: boolean;
    token?: string | null;
    /**
     * Per-call User-Agent override. `undefined` -> SDK_USER_AGENT (the common
     * case); a string pins that exact UA (WS ticket minting pins the UA the
     * WebSocket upgrade will send, since tickets are sha256(UA+IP)-bound);
     * `null` omits the header entirely (let the platform choose).
     */
    userAgent?: string | null;
}
export type FetchLike = (url: string, init: {
    method: string;
    headers: Record<string, string>;
    body?: string;
    signal: AbortSignal;
}) => Promise<Response>;
export declare class HarnessHttp {
    token: string | null;
    readonly timeoutMs: number;
    readonly maxRetries: number;
    readonly backoffBaseMs: number;
    readonly backoffCapMs: number;
    private readonly fetchImpl;
    private readonly sleepFn;
    constructor(options?: HarnessHttpOptions);
    /** Send one call; return the decoded JSON body (or text for non-JSON
     *  responses, null for empty ones). Throws ApiError on any non-2xx.
     *
     *  Transient failures (connection errors, 429/502/503/504) are retried with
     *  exponential backoff + jitter on idempotent methods only — except 429,
     *  which is always retryable (server pacing), honoring Retry-After.
     */
    request<T>(baseUrl: string, path: string, method: string, body?: unknown, params?: Record<string, unknown> | null, opts?: {
        auth?: boolean;
        token?: string | null;
        userAgent?: string | null;
    }): Promise<T>;
    /** One wire attempt: URL building is the generated stubs' contract. */
    private sendOnce;
    private decode;
    private retryAfterOrBackoff;
}
