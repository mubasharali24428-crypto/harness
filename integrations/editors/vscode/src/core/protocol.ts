// Pure Harness protocol client shared by every editor integration's tests.
// No editor APIs here: everything is injectable so it is verifiable without a host IDE.

export class HarnessError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "HarnessError";
  }
}

export interface Principal { userId: string; teamId: string; role: string }
export interface Project { id: string; name?: string }
export interface Hunk { id: string; lines: { kind: string; text: string }[] }
export interface ChangeSetFile {
  path: string;
  drift?: string;
  hunks: Hunk[];
  decisions: Record<string, { decision: string }>;
}
export interface ChangeSet { changeSetId: string; title: string; status: string; files: ChangeSetFile[] }

export interface Endpoints { identity: string; workflow: string; orchestrator: string }

/** Tokens only ever travel over https, or http to a loopback host (local dev). */
export function assertSafeBase(raw: string, label: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new HarnessError(`${label} URL is not valid`, "CONFIG_INVALID", 0);
  }
  const loopback = ["localhost", "127.0.0.1", "[::1]", "::1"].includes(url.hostname);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) {
    throw new HarnessError(
      `${label} must use https (http is allowed only for localhost)`,
      "INSECURE_TRANSPORT",
      0,
    );
  }
  if (url.username || url.password) {
    throw new HarnessError(`${label} URL must not embed credentials`, "CONFIG_INVALID", 0);
  }
  return url.origin + url.pathname.replace(/\/$/, "");
}

/** Remove anything that looks like a bearer credential from text bound for logs/UI. */
export function redact(text: string, token?: string | null): string {
  let out = text.replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/gi, "Bearer [REDACTED]");
  if (token && token.length >= 6) out = out.split(token).join("[REDACTED]");
  return out;
}

export function friendly(status: number, code: string, message: string): string {
  if (status === 401) return "Your Harness session is invalid or expired. Sign in again.";
  if (status === 403) return `Not allowed for your role (${code}).`;
  if (status === 404) return "Not found, or not visible to your team.";
  if (status === 409) return `Conflict (${code}): ${message}`;
  if (status === 0) return message;
  return `${message} (${code})`;
}

export class HarnessClient {
  private readonly ep: Endpoints;
  constructor(
    endpoints: Partial<Endpoints>,
    private readonly getToken: () => Promise<string | undefined>,
    private readonly doFetch: typeof fetch = (...a) => fetch(...a),
  ) {
    // Unset services stay unset: the matching capability reports "unsupported".
    this.ep = {
      identity: endpoints.identity ? assertSafeBase(endpoints.identity, "identityUrl") : "",
      workflow: endpoints.workflow ? assertSafeBase(endpoints.workflow, "workflowUrl") : "",
      orchestrator: endpoints.orchestrator
        ? assertSafeBase(endpoints.orchestrator, "orchestratorUrl")
        : "",
    };
  }

  private async call<T>(svc: keyof Endpoints, method: string, path: string, body?: unknown): Promise<T> {
    const base = this.ep[svc];
    if (!base) throw new HarnessError(`The ${svc} service is not configured`, "UNSUPPORTED", 0);
    const token = await this.getToken();
    if (!token) throw new HarnessError("Sign in first", "UNAUTHORIZED", 401);
    let res: Response;
    try {
      res = await this.doFetch(base + path, {
        method,
        headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        redirect: "error", // never follow a redirect with the bearer attached
      });
    } catch (e) {
      throw new HarnessError(
        redact(`Could not reach the ${svc} service: ${(e as Error).message}`, token),
        "UNREACHABLE",
        0,
      );
    }
    let parsed: unknown = null;
    try {
      parsed = await res.json();
    } catch {
      parsed = null;
    }
    if (!res.ok) {
      const env = (parsed as { error?: { code?: string; message?: string }; detail?: { error?: { code?: string; message?: string } } } | null);
      const err = env?.error ?? env?.detail?.error;
      const code = err?.code ?? "UNKNOWN";
      const message = redact(err?.message ?? `HTTP ${res.status}`, token);
      throw new HarnessError(friendly(res.status, code, message), code, res.status);
    }
    return parsed as T;
  }

  me(): Promise<Principal> { return this.call("identity", "GET", "/api/v1/me"); }
  async projects(): Promise<Project[]> {
    const r = await this.call<{ items?: Project[] }>("identity", "GET", "/api/v1/projects?limit=100");
    return r.items ?? [];
  }
  async codeSessions(projectId: string): Promise<{ sessionId: string; branch: string }[]> {
    const r = await this.call<{ items: { sessionId: string; branch: string }[] }>(
      "workflow", "GET", `/api/v1/developer-workflow/code-sessions?projectId=${encodeURIComponent(projectId)}`);
    return r.items;
  }
  async changeSets(sessionId: string): Promise<ChangeSet[]> {
    const r = await this.call<{ items: ChangeSet[] }>(
      "workflow", "GET", `/api/v1/developer-workflow/code-sessions/${encodeURIComponent(sessionId)}/change-sets`);
    return r.items;
  }
  decide(changeSetId: string, decisions: { path: string; hunkId: string; decision: string }[]): Promise<ChangeSet> {
    return this.call("workflow", "POST",
      `/api/v1/developer-workflow/change-sets/${encodeURIComponent(changeSetId)}/decisions`, { decisions });
  }
  runStatus(runId: string): Promise<Record<string, unknown>> {
    return this.call("orchestrator", "GET", `/api/v1/orchestrator/runs/${encodeURIComponent(runId)}/status`);
  }
  approvePlan(runId: string, planId: string): Promise<{ status: string }> {
    return this.call("orchestrator", "POST",
      `/api/v1/orchestrator/runs/${encodeURIComponent(runId)}/plan/approve`, { planId });
  }
}

export function pendingHunks(cs: ChangeSet): number {
  return cs.files.reduce(
    (n, f) => n + f.hunks.filter((h) => (f.decisions[h.id]?.decision ?? "pending") === "pending").length, 0);
}
