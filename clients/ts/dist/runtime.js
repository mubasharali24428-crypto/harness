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
import { ApiError, parseApiError } from "./errors.js";
import { backoffDelay } from "./backoff.js";
/** Extraction of section7/src/lib/backoff.ts constants (H-012). */
export const BACKOFF_BASE_MS = 1000; // BACKOFF_BASE_DELAY_MS = 1000
export const BACKOFF_CAP_MS = 30_000; // BACKOFF_MAX_DELAY_MS  = 30_000
export const SDK_USER_AGENT = "harness-ts-sdk/1.0.0rc1";
/** Section service name -> default local port (deploy/docker-compose.yml). */
export const SERVICE_PORTS = {
    identity: 8001,
    github: 8002,
    router: 8003,
    orchestrator: 8004,
    sandbox: 8005,
    meeting: 8006,
    review: 8008,
};
/** Default base URL for a section: HARNESS_<NAME>_URL env var, else
 *  `http://localhost:<default port>`. (Node-only env read; browsers pass
 *  explicit base URLs via Harness options instead.) */
export function baseUrlFor(service) {
    let env;
    try {
        // Guarded so bundling this module for a browser never crashes on a
        // missing `process` global.
        env =
            typeof process !== "undefined" && process.env
                ? process.env[`HARNESS_${service.toUpperCase()}_URL`]
                : undefined;
    }
    catch {
        env = undefined;
    }
    if (env)
        return env.replace(/\/+$/, "");
    return `http://localhost:${SERVICE_PORTS[service]}`;
}
const RETRYABLE_STATUS = new Set([429, 502, 503, 504]);
const IDEMPOTENT_METHODS = new Set(["GET", "HEAD", "PUT", "DELETE"]);
export class HarnessHttp {
    token;
    timeoutMs;
    maxRetries;
    backoffBaseMs;
    backoffCapMs;
    fetchImpl;
    sleepFn;
    constructor(options = {}) {
        this.token = options.token ?? null;
        this.timeoutMs = options.timeoutMs ?? 15_000;
        this.maxRetries = options.maxRetries ?? 3;
        this.backoffBaseMs = options.backoffBaseMs ?? BACKOFF_BASE_MS;
        this.backoffCapMs = options.backoffCapMs ?? BACKOFF_CAP_MS;
        this.fetchImpl = options.fetchImpl ?? ((url, init) => fetch(url, init));
        this.sleepFn = options.sleepFn ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
    }
    /** Send one call; return the decoded JSON body (or text for non-JSON
     *  responses, null for empty ones). Throws ApiError on any non-2xx.
     *
     *  Transient failures (connection errors, 429/502/503/504) are retried with
     *  exponential backoff + jitter on idempotent methods only — except 429,
     *  which is always retryable (server pacing), honoring Retry-After.
     */
    async request(baseUrl, path, method, body, params, opts = {}) {
        const upper = method.toUpperCase();
        let attempt = 0;
        for (;;) {
            attempt += 1;
            let res;
            try {
                res = await this.sendOnce(baseUrl, path, upper, body, params, opts);
            }
            catch (err) {
                if (err instanceof ApiError)
                    throw err;
                // Transport-level failure (refused / aborted / DNS): retry idempotent
                // calls, then surface as TRANSPORT_ERROR exactly like the Python SDK.
                if (attempt > this.maxRetries || !IDEMPOTENT_METHODS.has(upper)) {
                    throw new ApiError(`transport error: ${err instanceof Error ? err.message : String(err)}`, "TRANSPORT_ERROR");
                }
                await this.sleepFn(backoffDelay(attempt, this.backoffBaseMs, this.backoffCapMs));
                continue;
            }
            if (!res.ok) {
                const raw = await res.text();
                let parsed = null;
                if (raw) {
                    try {
                        parsed = JSON.parse(raw);
                    }
                    catch {
                        parsed = null;
                    }
                }
                const { code, message } = parseApiError(res.status, parsed);
                const apiErr = new ApiError(message, code, res.status, parsed);
                const mayRetry = attempt <= this.maxRetries &&
                    (IDEMPOTENT_METHODS.has(upper) || res.status === 429);
                if (!RETRYABLE_STATUS.has(res.status) || !mayRetry)
                    throw apiErr;
                await this.sleepFn(this.retryAfterOrBackoff(res.headers.get("retry-after"), attempt));
                continue;
            }
            return (await this.decode(res));
        }
    }
    /** One wire attempt: URL building is the generated stubs' contract. */
    async sendOnce(baseUrl, path, method, body, params, opts) {
        const url = new URL(`${baseUrl.replace(/\/+$/, "")}${path}`);
        // Drop unset optional query params (undefined/null sentinel from stubs).
        for (const [k, v] of Object.entries(params ?? {})) {
            if (v === undefined || v === null)
                continue;
            url.searchParams.set(k, String(v));
        }
        const headers = {};
        const ua = opts.userAgent === undefined ? SDK_USER_AGENT : opts.userAgent;
        if (ua !== null)
            headers["user-agent"] = ua;
        const effectiveToken = opts.token !== undefined ? opts.token : this.token;
        if ((opts.auth ?? true) && effectiveToken) {
            headers.authorization = `Bearer ${effectiveToken}`;
        }
        // Optional-body operations (body: undefined in the generated stubs) still
        // send a JSON object — FastAPI endpoints with required Pydantic bodies
        // 422 on a missing body, and Python's httpx json=None behaves the same
        // way as {} here. Real "no content" requests pass body === null.
        let wireBody;
        if (body !== undefined) {
            wireBody = JSON.stringify(body);
            headers["content-type"] = "application/json";
        }
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), this.timeoutMs);
        try {
            return await this.fetchImpl(url.toString(), {
                method,
                headers,
                body: wireBody,
                signal: controller.signal,
            });
        }
        finally {
            clearTimeout(timer);
        }
    }
    async decode(res) {
        if (res.status === 204)
            return null;
        const text = await res.text();
        if (!text)
            return null;
        const ctype = res.headers.get("content-type") ?? "";
        if (!ctype.includes("application/json"))
            return text;
        try {
            return JSON.parse(text);
        }
        catch {
            return text;
        }
    }
    retryAfterOrBackoff(retryAfter, attempt) {
        if (retryAfter) {
            const seconds = Number.parseFloat(retryAfter);
            if (!Number.isNaN(seconds))
                return Math.max(0, seconds) * 1000;
        }
        return backoffDelay(attempt, this.backoffBaseMs, this.backoffCapMs);
    }
}
