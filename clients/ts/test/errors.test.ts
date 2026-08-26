/**
 * Unit tests: frozen error-envelope parsing (extraction of section7
 * api.ts parseApiError) + backoff arithmetic (extraction of backoff.ts).
 * Mirrors clients/python/tests/test_sdk.py's tables.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { ApiError, parseApiError } from "../src/errors.js";
import {
  BACKOFF_BASE_DELAY_MS,
  BACKOFF_MAX_DELAY_MS,
  backoffDelay,
  exponentialDelay,
} from "../src/backoff.js";

describe("parseApiError", () => {
  it("parses the direct contract envelope", () => {
    const out = parseApiError(403, {
      error: { code: "OWNERSHIP_REQUIRED", message: "prove it" },
    });
    assert.equal(out.code, "OWNERSHIP_REQUIRED");
    assert.equal(out.message, "prove it");
  });

  it("parses the FastAPI-wrapped envelope", () => {
    const out = parseApiError(404, {
      detail: { error: { code: "NOT_FOUND", message: "nope" } },
    });
    assert.equal(out.code, "NOT_FOUND");
    assert.equal(out.message, "nope");
  });

  it("prefers the contract envelope when both shapes ship", () => {
    const out = parseApiError(403, {
      error: { code: "X", message: "y" },
      detail: { error: { code: "X", message: "y" } },
    });
    assert.equal(out.code, "X");
    assert.equal(out.message, "y");
  });

  it("falls back to the plain string detail", () => {
    const out = parseApiError(400, { detail: "bad body" });
    assert.equal(out.code, "UNKNOWN");
    assert.equal(out.message, "bad body");
  });

  it("falls through to generic UNKNOWN for junk bodies", () => {
    for (const body of [null, "not a dict", {}, [1, 2], 42]) {
      const out = parseApiError(500, body);
      assert.equal(out.code, "UNKNOWN");
      assert.equal(out.message, "Request failed with status 500");
    }
  });

  it("defaults missing envelope fields", () => {
    const out = parseApiError(418, { error: {} });
    assert.equal(out.code, "UNKNOWN");
    assert.equal(out.message, "Request failed with status 418");
  });

  it("ApiError carries code/status/body", () => {
    const err = new ApiError("boom", "ROLE_DENIED", 403, { error: {} });
    assert.equal(err.name, "ApiError");
    assert.equal(err.code, "ROLE_DENIED");
    assert.equal(err.status, 403);
    assert.deepEqual(err.body, { error: {} });
  });
});

describe("backoff (extraction pins backoff.ts constants)", () => {
  it("exponentialDelay sequence: 1s, 2s, 4s … capped at 30s", () => {
    assert.deepEqual(
      [1, 2, 3, 4, 5, 6].map((n) => exponentialDelay(n)),
      [1000, 2000, 4000, 8000, 16_000, 30_000],
    );
    assert.equal(exponentialDelay(20), BACKOFF_MAX_DELAY_MS);
    assert.equal(exponentialDelay(0), 0);
  });

  it("backoffDelay stays within [0, ceiling] (full jitter)", () => {
    for (let attempt = 1; attempt <= 8; attempt += 1) {
      const delay = backoffDelay(attempt);
      assert.ok(delay >= 0 && delay < exponentialDelay(attempt));
      assert.ok(Number.isInteger(delay));
    }
  });

  it("honors custom base/cap", () => {
    assert.equal(exponentialDelay(3, 10, 25), 25); // 10*4 capped
    assert.ok(backoffDelay(3, 10, 25) < 25);
  });

  it("exports the documented base constant", () => {
    assert.equal(BACKOFF_BASE_DELAY_MS, 1000);
  });
});
