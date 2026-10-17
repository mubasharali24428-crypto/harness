import { createHmac, timingSafeEqual } from "node:crypto";
import { canonicalJson } from "./digest.js";
import { WorkerError, type SessionIdentity } from "./types.js";

/**
 * S5→browser capability tokens (wire format shared with
 * section5/src/harness_sandbox/browser_cap.py).
 *
 * Token: bcap1.<base64url(payloadJson)>.<base64url(HMAC-SHA256)>
 * The HMAC covers the literal prefix + payload segment. Payload JSON uses
 * sorted keys and compact separators, matching Python's
 * json.dumps(sort_keys=True, separators=(",", ":")).
 *
 * Semantics: bearer token, expiring, bound to op=“browser”, one sandbox task,
 * one team/project/run, and a bounded set of origins. A token is an
 * authorization for those origins only — it is NOT permission to contact
 * arbitrary hosts, and verification binds it to the session identity so a
 * token from another run cannot be replayed here.
 */

export const CAPABILITY_PREFIX = "bcap1";
export const CAPABILITY_OP = "browser";

export interface CapabilityPayload {
  v: 1;
  op: "browser";
  taskId: string;
  teamId: string;
  projectId: string;
  runId: string;
  allowedOrigins: string[];
  iat: number;
  exp: number;
  nonce: string;
}

export interface CapabilityVerifyOptions {
  secret: string;
  /** Unix seconds; injectable for tests. */
  now: number;
  binding: SessionIdentity;
}

function b64url(data: Buffer | string): string {
  return Buffer.from(data).toString("base64url");
}

export function mintCapability(secret: string, payload: CapabilityPayload): string {
  const body = b64url(canonicalJson(payload));
  const sig = createHmac("sha256", secret).update(`${CAPABILITY_PREFIX}.${body}`).digest();
  return `${CAPABILITY_PREFIX}.${body}.${b64url(sig)}`;
}

export function verifyCapability(token: string, opts: CapabilityVerifyOptions): CapabilityPayload {
  if (typeof token !== "string") {
    throw new WorkerError("CAPABILITY_INVALID", "capability token is required");
  }
  const parts = token.split(".");
  if (parts.length !== 3 || parts[0] !== CAPABILITY_PREFIX) {
    throw new WorkerError("CAPABILITY_INVALID", "malformed capability token");
  }
  const [, body, sig] = parts as [string, string, string];
  const expected = createHmac("sha256", opts.secret)
    .update(`${CAPABILITY_PREFIX}.${body}`)
    .digest();
  let provided: Buffer;
  try {
    provided = Buffer.from(sig, "base64url");
  } catch {
    throw new WorkerError("CAPABILITY_INVALID", "malformed capability signature");
  }
  if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) {
    throw new WorkerError("CAPABILITY_INVALID", "capability signature mismatch");
  }

  let payload: CapabilityPayload;
  try {
    payload = JSON.parse(Buffer.from(body, "base64url").toString("utf8")) as CapabilityPayload;
  } catch {
    throw new WorkerError("CAPABILITY_INVALID", "capability payload is not JSON");
  }
  if (payload.v !== 1 || payload.op !== CAPABILITY_OP) {
    throw new WorkerError("CAPABILITY_INVALID", "unsupported capability op/version");
  }
  if (opts.now >= payload.exp) {
    throw new WorkerError("CAPABILITY_EXPIRED", "capability token has expired");
  }
  const { binding } = opts;
  const bindingOk =
    payload.teamId === binding.teamId &&
    payload.projectId === binding.projectId &&
    payload.runId === binding.runId;
  if (!bindingOk) {
    throw new WorkerError("CAPABILITY_SCOPE", "capability token is for a different run");
  }
  return payload;
}

/** Navigation scope: an action may only reach origins the token grants. */
export function assertCapabilityOrigin(payload: CapabilityPayload, origin: string): void {
  if (!payload.allowedOrigins.includes(origin)) {
    throw new WorkerError("CAPABILITY_SCOPE", `origin '${origin}' is outside the capability grant`, {
      allowedOrigins: payload.allowedOrigins,
    });
  }
}
