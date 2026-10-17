import { digestOf, newId } from "./digest.js";
import { WorkerError, type SessionIdentity } from "./types.js";

/**
 * One-time approval grants for browser actions with external side effects.
 *
 * A grant is bound to (team, project, run, operation, argument digest) with an
 * expiry and an intended audience. Consumption is single-use: replay is a
 * distinct, loudly-named error. The argument digest covers the exact action
 * payload, so an approval for "submit newsletter form" cannot be replayed for
 * a different form or different field values.
 */

export const BROWSER_APPROVAL_AUDIENCE = "browser-worker";

export interface ApprovalRequest {
  identity: SessionIdentity;
  operation: string;
  arguments: unknown;
  ttlSeconds?: number;
  requestedBy: string;
}

export interface ApprovalGrant {
  approvalId: string;
  audience: string;
  operation: string;
  argumentsDigest: string;
  issuedAtMs: number;
  expiresAtMs: number;
  requestedBy: string;
  identity: SessionIdentity;
}

export interface ApprovalBinding {
  identity: SessionIdentity;
  operation: string;
  arguments: unknown;
}

export const DEFAULT_APPROVAL_TTL_SECONDS = 120;

export class ApprovalStore {
  private readonly grants = new Map<string, { grant: ApprovalGrant; consumed: boolean }>();
  private readonly now: () => number;

  constructor(now: () => number = () => Date.now()) {
    this.now = now;
  }

  issue(request: ApprovalRequest): ApprovalGrant {
    const ttl = request.ttlSeconds ?? DEFAULT_APPROVAL_TTL_SECONDS;
    if (!(ttl > 0)) throw new WorkerError("BAD_REQUEST", "approval ttl must be positive");
    const nowMs = this.now();
    const grant: ApprovalGrant = {
      approvalId: newId("apr"),
      audience: BROWSER_APPROVAL_AUDIENCE,
      operation: request.operation,
      argumentsDigest: digestOf(request.arguments ?? null),
      issuedAtMs: nowMs,
      expiresAtMs: nowMs + ttl * 1000,
      requestedBy: request.requestedBy,
      identity: { ...request.identity },
    };
    this.grants.set(grant.approvalId, { grant, consumed: false });
    return grant;
  }

  /** Validate + atomically consume. Throws WorkerError on any mismatch. */
  consume(approvalId: string, binding: ApprovalBinding): ApprovalGrant {
    const entry = this.grants.get(approvalId);
    if (!entry) {
      throw new WorkerError("APPROVAL_INVALID", `unknown approval '${approvalId}'`);
    }
    const { grant } = entry;
    if (entry.consumed) {
      throw new WorkerError("APPROVAL_REPLAYED", `approval '${approvalId}' was already used`);
    }
    if (this.now() >= grant.expiresAtMs) {
      throw new WorkerError("APPROVAL_EXPIRED", `approval '${approvalId}' has expired`);
    }
    const identityMatches =
      grant.identity.teamId === binding.identity.teamId &&
      grant.identity.projectId === binding.identity.projectId &&
      grant.identity.runId === binding.identity.runId;
    if (!identityMatches) {
      throw new WorkerError("APPROVAL_INVALID", "approval does not belong to this session");
    }
    if (grant.operation !== binding.operation) {
      throw new WorkerError(
        "APPROVAL_INVALID",
        `approval is for operation '${grant.operation}', not '${binding.operation}'`,
      );
    }
    if (grant.argumentsDigest !== digestOf(binding.arguments ?? null)) {
      throw new WorkerError("APPROVAL_INVALID", "approval arguments do not match this action");
    }
    entry.consumed = true;
    return grant;
  }

  prune(nowMs?: number): void {
    const at = nowMs ?? this.now();
    for (const [id, entry] of this.grants) {
      if (entry.consumed || entry.grant.expiresAtMs <= at) this.grants.delete(id);
    }
  }
}
