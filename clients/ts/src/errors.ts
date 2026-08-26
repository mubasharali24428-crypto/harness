/**
 * Error types for the Harness TypeScript SDK.
 *
 * `ApiError` mirrors the frozen error envelope from contracts/api-conventions.md:
 *
 *     {"error": {"code": "<machine_code>", "message": "<human>"}}
 *
 * Sections built on FastAPI's HTTPException wrap that envelope as
 * `{"detail": {"error": {...}}}` — both shapes are parsed.
 *
 * EXTRACTION NOTE (mission M19-N5 / M20-T5 law: reuse, don't duplicate):
 * `parseApiError` is the verbatim extraction of section7/src/lib/api.ts
 * `parseApiError` — same three accepted shapes plus the generic fallback.
 * The Python SDK carries the identical logic in harness_sdk/errors.py.
 */

export class ApiError extends Error {
  /** Machine-readable error code from the envelope ("UNKNOWN" when absent). */
  code: string;
  /** HTTP status number (0 when the failure never produced a response). */
  status: number;
  /** Raw decoded response body, when one arrived. */
  body: unknown;

  constructor(message: string, code: string, status = 0, body: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.body = body;
  }
}

interface ApiErrorBody {
  error?: { code?: string; message?: string };
  detail?: { error?: { code?: string; message?: string } } | string | unknown[];
}

/** Pure: turns a non-2xx status + decoded JSON body into message + code.
 *
 *  Handles, in order:
 *  1. direct contract envelope:  `{"error": {"code", "message"}}`
 *  2. FastAPI-wrapped envelope:  `{"detail": {"error": {"code", "message"}}}`
 *  3. FastAPI string detail:     `{"detail": "..."}`
 *  4. anything else (non-dict / undecodable): generic message + `UNKNOWN`.
 */
export function parseApiError(
  status: number,
  body: unknown,
): { message: string; code: string } {
  const fallback = {
    message: `Request failed with status ${status}`,
    code: "UNKNOWN",
  };
  if (!body || typeof body !== "object") return fallback;

  const errBody = body as ApiErrorBody;

  // 1. Direct contract error envelope: { error: { code, message } }
  if (errBody.error && typeof errBody.error === "object") {
    return {
      code: errBody.error.code ?? "UNKNOWN",
      message: errBody.error.message ?? fallback.message,
    };
  }

  // 2. FastAPI wrapped HTTPException detail: { detail: { error: {…} } }
  if (errBody.detail && typeof errBody.detail === "object" && !Array.isArray(errBody.detail)) {
    const detailObj = errBody.detail as { error?: { code?: string; message?: string } };
    if (detailObj.error && typeof detailObj.error === "object") {
      return {
        code: detailObj.error.code ?? "UNKNOWN",
        message: detailObj.error.message ?? fallback.message,
      };
    }
  }

  // 3. FastAPI simple string detail: { detail: "error message" }
  if (typeof errBody.detail === "string" && errBody.detail !== "") {
    return { message: errBody.detail, code: "UNKNOWN" };
  }

  return fallback;
}
