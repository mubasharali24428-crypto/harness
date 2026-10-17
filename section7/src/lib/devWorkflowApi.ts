// A4 developer-workflow service client (/api/v1/developer-workflow). Bearer identity only;
// the server derives team/principal — nothing here is trusted for authorization.
import { ApiError, parseApiError } from "./api";
import type { ChangeSet, Decision } from "./reviewModel";

export const DEV_WORKFLOW_BASE =
  process.env.NEXT_PUBLIC_DEV_WORKFLOW_URL ?? "http://localhost:8014";
const PREFIX = "/api/v1/developer-workflow";

export interface DevWorkflowApi {
  getChangeSet(id: string): Promise<ChangeSet>;
  decide(id: string, decisions: { path: string; hunkId: string; decision: Decision; comment?: string }[]): Promise<ChangeSet>;
  apply(id: string, idempotencyKey: string): Promise<{ status: string; files: { path: string; status: string }[] }>;
  cancel(id: string): Promise<ChangeSet>;
}

export function createDevWorkflowApi(opts: {
  base?: string;
  getToken: () => string | null;
  fetchImpl?: typeof fetch;
}): DevWorkflowApi {
  const base = (opts.base ?? DEV_WORKFLOW_BASE).replace(/\/$/, "");
  const doFetch = opts.fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a));

  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const token = opts.getToken();
    if (!token) throw new ApiError("Sign in to review changes.", "UNAUTHORIZED", 401);
    let res: Response;
    try {
      res = await doFetch(`${base}${PREFIX}${path}`, {
        method,
        headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new ApiError("The developer-workflow service is unreachable.", "UPSTREAM_UNREACHABLE", 503);
    }
    let parsed: unknown = null;
    try {
      parsed = await res.json();
    } catch {
      parsed = null;
    }
    if (!res.ok) {
      const { message, code } = parseApiError(res.status, parsed);
      throw new ApiError(message, code, res.status);
    }
    return parsed as T;
  }

  return {
    getChangeSet: (id) => call("GET", `/change-sets/${encodeURIComponent(id)}`),
    decide: (id, decisions) => call("POST", `/change-sets/${encodeURIComponent(id)}/decisions`, { decisions }),
    apply: (id, idempotencyKey) => call("POST", `/change-sets/${encodeURIComponent(id)}/apply`, { idempotencyKey }),
    cancel: (id) => call("POST", `/change-sets/${encodeURIComponent(id)}/cancel`, {}),
  };
}
