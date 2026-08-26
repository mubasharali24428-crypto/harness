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
export const SERVICE_PORTS: Record<string, number> = {
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
export function baseUrlFor(service: string): string {
  let env: string | undefined;
  try {
    // Guarded so bundling this module for a browser never crashes on a
    // missing `process` global.
    env =
      typeof process !== "undefined" && process.env
        ? process.env[`HARNESS_${service.toUpperCase()}_URL`]
        : undefined;
  } catch {
    env = undefined;
  }
  if (env) return env.replace(/\/+$/, "");
  return `http://localhost:${SERVICE_PORTS[service]}`;
}

const RETRYABLE_STATUS = new Set([429, 502, 503, 504]);
const IDEMPOTENT_METHODS = new Set(["GET", "HEAD", "PUT", "DELETE"]);

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

export type FetchLike = (
  url: string,
  init: { method: string; headers: Record<string, string>; body?: string; signal: AbortSignal },
) => Promise<Response>;

interface ResponseLike {
  ok: boolean;
  status: number;
  text(): Promise<string>;
  headers: Headers;
}

export class HarnessHttp {
  token: string | null;
  readonly timeoutMs: number;
  readonly maxRetries: number;
  readonly backoffBaseMs: number;
  readonly backoffCapMs: number;
  private readonly fetchImpl: FetchLike;
  private readonly sleepFn: (ms: number) => Promise<void>;

  constructor(options: HarnessHttpOptions = {}) {
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
  async request<T>(
    baseUrl: string,
    path: string,
    method: string,
    body?: unknown,
    params?: Record<string, unknown> | null,
    opts: { auth?: boolean; token?: string | null; userAgent?: string | null } = {},
  ): Promise<T> {
    const upper = method.toUpperCase();
    let attempt = 0;
    for (;;) {
      attempt += 1;
      let res: ResponseLike;
      try {
        res = await this.sendOnce(baseUrl, path, upper, body, params, opts);
      } catch (err) {
        if (err instanceof ApiError) throw err;
        // Transport-level failure (refused / aborted / DNS): retry idempotent
        // calls, then surface as TRANSPORT_ERROR exactly like the Python SDK.
        if (attempt > this.maxRetries || !IDEMPOTENT_METHODS.has(upper)) {
          throw new ApiError(
            `transport error: ${err instanceof Error ? err.message : String(err)}`,
            "TRANSPORT_ERROR",
          );
        }
        await this.sleepFn(backoffDelay(attempt, this.backoffBaseMs, this.backoffCapMs));
        continue;
      }
      if (!res.ok) {
        const raw = await res.text();
        let parsed: unknown = null;
        if (raw) {
          try {
            parsed = JSON.parse(raw);
          } catch {
            parsed = null;
          }
        }
        const { code, message } = parseApiError(res.status, parsed);
        const apiErr = new ApiError(message, code, res.status, parsed);
        const mayRetry =
          attempt <= this.maxRetries &&
          (IDEMPOTENT_METHODS.has(upper) || res.status === 429);
        if (!RETRYABLE_STATUS.has(res.status) || !mayRetry) throw apiErr;
        await this.sleepFn(
          this.retryAfterOrBackoff(res.headers.get("retry-after"), attempt),
        );
        continue;
      }
      return (await this.decode<T>(res)) as T;
    }
  }

  /** One wire attempt: URL building is the generated stubs' contract. */
  private async sendOnce(
    baseUrl: string,
    path: string,
    method: string,
    body: unknown,
    params: Record<string, unknown> | null | undefined,
    opts: { auth?: boolean; token?: string | null; userAgent?: string | null },
  ): Promise<ResponseLike> {
    const url = new URL(`${baseUrl.replace(/\/+$/, "")}${path}`);
    // Drop unset optional query params (undefined/null sentinel from stubs).
    for (const [k, v] of Object.entries(params ?? {})) {
      if (v === undefined || v === null) continue;
      url.searchParams.set(k, String(v));
    }
    const headers: Record<string, string> = {};
    const ua =
      opts.userAgent === undefined ? SDK_USER_AGENT : opts.userAgent;
    if (ua !== null) headers["user-agent"] = ua;
    const effectiveToken = opts.token !== undefined ? opts.token : this.token;
    if ((opts.auth ?? true) && effectiveToken) {
      headers.authorization = `Bearer ${effectiveToken}`;
    }
    // Optional-body operations (body: undefined in the generated stubs) still
    // send a JSON object — FastAPI endpoints with required Pydantic bodies
    // 422 on a missing body, and Python's httpx json=None behaves the same
    // way as {} here. Real "no content" requests pass body === null.
    let wireBody: string | undefined;
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
    } finally {
      clearTimeout(timer);
    }
  }

  private async decode<T>(res: ResponseLike): Promise<T | string | null> {
    if (res.status === 204) return null;
    const text = await res.text();
    if (!text) return null;
    const ctype = res.headers.get("content-type") ?? "";
    if (!ctype.includes("application/json")) return text as unknown as T;
    try {
      return JSON.parse(text) as T;
    } catch {
      return text as unknown as T;
    }
  }

  private retryAfterOrBackoff(retryAfter: string | null, attempt: number): number {
    if (retryAfter) {
      const seconds = Number.parseFloat(retryAfter);
      if (!Number.isNaN(seconds)) return Math.max(0, seconds) * 1000;
    }
    return backoffDelay(attempt, this.backoffBaseMs, this.backoffCapMs);
  }
}
