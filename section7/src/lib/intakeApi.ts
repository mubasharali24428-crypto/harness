// Issue/PR intake client (section2, /api/v1/developer-workflow/intake). Bearer identity only.
import { ApiError, parseApiError } from "./api";

export interface Intake {
  id: string;
  status: "pending_approval" | "approved" | "rejected";
  provider: string;
  kind: string;
  title: string;
  body: string;
  url: string;
  actor: string;
  trigger: string;
  dispatch?: string;
}
export interface IntakeApi {
  list(): Promise<Intake[]>;
  decide(id: string, approve: boolean): Promise<Intake>;
}

const PREFIX = "/api/v1/developer-workflow/intake";

export function createIntakeApi(opts: {
  base: string;
  getToken: () => string | null;
  fetchImpl?: typeof fetch;
}): IntakeApi {
  const base = opts.base.replace(/\/$/, "");
  const doFetch = opts.fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
  async function call<T>(method: string, path: string): Promise<T> {
    const token = opts.getToken();
    if (!token) throw new ApiError("Sign in to see the task inbox.", "UNAUTHORIZED", 401);
    let res: Response;
    try {
      res = await doFetch(`${base}${PREFIX}${path}`, { method, headers: { authorization: `Bearer ${token}` } });
    } catch {
      throw new ApiError("The intake service is unreachable.", "UPSTREAM_UNREACHABLE", 503);
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
    list: async () => (await call<{ items: Intake[] }>("GET", "/intakes")).items,
    decide: (id, approve) => call("POST", `/intakes/${encodeURIComponent(id)}/${approve ? "approve" : "reject"}`),
  };
}
