import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { readFile } from "node:fs/promises";
import { clickApprovalArguments, executeAction } from "./actions.js";
import { WorkerError, type BrowserAction, type TargetSpec } from "./types.js";
import type { SessionManager, WorkerSession } from "./session.js";
import type { PreviewRegistry } from "./preview.js";

/**
 * HAP/1 HTTP adapter under /api/v1/computer-use. Identity and team come ONLY
 * from the authenticated principal (Section 1 `GET /api/v1/me`), never the
 * body. Sessions are owned by the team that opened them; other teams get 404.
 * Approvals are admin-only (a human decision); engineers drive actions.
 */

export interface Principal {
  userId: string;
  teamId: string;
  role: "admin" | "engineer" | "viewer" | "guest";
}

export interface ServerOptions {
  manager: SessionManager;
  authenticate: (bearer: string) => Promise<Principal | null>;
  /** True when the team owns the project (Section 1 project list). */
  projectOwned: (bearer: string, principal: Principal, projectId: string) => Promise<boolean>;
  capabilitySecret: string;
  /** Private-network origins must be registered here per team; fail closed. */
  previews: PreviewRegistry;
  /** Operator-approved public origins. Empty by default: no public egress. */
  publicAllowlist?: string[];
  baselineDir?: string;
}

const PREFIX = "/api/v1/computer-use";
const MAX_BODY = 1_000_000;

/** Section 1 bearer introspection + project ownership over authenticated HTTP. */
export function section1Auth(baseUrl: string): Pick<ServerOptions, "authenticate" | "projectOwned"> {
  const get = (path: string, bearer: string) =>
    fetch(`${baseUrl}${path}`, { headers: { authorization: `Bearer ${bearer}` } });
  return {
    async authenticate(bearer) {
      const res = await get("/api/v1/me", bearer).catch(() => null);
      if (!res || !res.ok) return null;
      return (await res.json()) as Principal;
    },
    async projectOwned(bearer, _principal, projectId) {
      const res = await get("/api/v1/projects", bearer).catch(() => null);
      if (!res || !res.ok) return false;
      const body = (await res.json()) as { items: { id: string }[] };
      return body.items.some((p) => p.id === projectId);
    },
  };
}

function send(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

function fail(res: ServerResponse, status: number, code: string, message: string): void {
  send(res, status, { error: { code, message } });
}

async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY) throw new WorkerError("BAD_REQUEST", "request body too large");
    chunks.push(chunk as Buffer);
  }
  try {
    const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
    return parsed as Record<string, unknown>;
  } catch {
    throw new WorkerError("BAD_REQUEST", "body must be a JSON object");
  }
}

const OPERATIONS = new Set([
  "navigate", "click", "type", "press", "scroll", "hover", "selectOption",
  "waitFor", "screenshot", "observe", "tap", "diagnostics", "pwaCheck", "visualCheck", "e2e",
]);

export function createComputerUseServer(opts: ServerOptions): Server {
  const ownSession = (principal: Principal, id: string): WorkerSession => {
    const session = opts.manager.get(id);
    if (session.identity.teamId !== principal.teamId) {
      throw new WorkerError("SESSION_NOT_FOUND", `unknown session '${id}'`);
    }
    return session;
  };

  return createServer((req, res) => {
    void handle(req, res).catch((error: unknown) => {
      if (error instanceof WorkerError) {
        const status = error.code === "SESSION_NOT_FOUND" ? 404 : error.code === "BAD_REQUEST" ? 400 : 422;
        return fail(res, status, error.code.toLowerCase(), error.message);
      }
      return fail(res, 500, "internal", "internal error");
    });
  });

  async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", "http://worker.local");
    const path = url.pathname;
    if (!path.startsWith(`${PREFIX}/`)) return fail(res, 404, "not_found", "no such route");

    const bearer = (req.headers.authorization ?? "").replace(/^Bearer\s+/i, "").trim();
    const principal = bearer ? await opts.authenticate(bearer) : null;
    if (!principal) return fail(res, 401, "unauthenticated", "valid bearer token required");

    const parts = path.slice(PREFIX.length + 1).split("/").filter(Boolean);
    const method = req.method ?? "GET";
    const canDrive = principal.role === "admin" || principal.role === "engineer";

    if (method === "POST" && parts.length === 1 && parts[0] === "previews") {
      if (!canDrive) return fail(res, 403, "forbidden", "role may not register previews");
      const body = await readJson(req);
      const origin = opts.previews.register(principal.teamId, Number(body.port));
      return send(res, 201, { origin });
    }

    if (method === "POST" && parts.length === 1 && parts[0] === "sessions") {
      if (!canDrive) return fail(res, 403, "forbidden", "role may not drive the browser");
      const body = await readJson(req);
      const projectId = String(body.projectId ?? "");
      const runId = String(body.runId ?? "");
      if (!(await opts.projectOwned(bearer, principal, projectId))) {
        return fail(res, 404, "project_not_found", "project not found");
      }
      opts.previews.assertOriginsAllowed(
        principal.teamId,
        Array.isArray(body.allowedOrigins) ? (body.allowedOrigins as string[]) : [],
        opts.publicAllowlist ?? [],
      );
      const session = await opts.manager.open(
        { teamId: principal.teamId, projectId, runId },
        {
          allowedOrigins: body.allowedOrigins as string[],
          viewport: body.viewport as { width: number; height: number } | undefined,
          deviceScaleFactor: body.deviceScaleFactor as number | undefined,
          profile: body.profile as string | undefined,
        },
      );
      return send(res, 201, { sessionId: session.sessionId, allowedOrigins: session.allowedOrigins });
    }

    if (parts[0] === "sessions" && parts[1]) {
      const session = ownSession(principal, parts[1]);

      if (method === "DELETE" && parts.length === 2) {
        if (!canDrive) return fail(res, 403, "forbidden", "role may not drive the browser");
        await opts.manager.close(session.sessionId);
        return send(res, 200, { ok: true });
      }

      if (method === "POST" && parts[2] === "actions") {
        if (!canDrive) return fail(res, 403, "forbidden", "role may not drive the browser");
        const body = await readJson(req);
        const operation = String(body.operation ?? "");
        const args = (body.arguments ?? {}) as Record<string, unknown>;
        if (!OPERATIONS.has(operation)) {
          return send(res, 200, {
            schemaVersion: "hap/1", requestId: body.requestId ?? null, status: "unsupported",
            artifacts: [], error: { code: "UNSUPPORTED", message: `unknown operation '${operation}'` },
          });
        }
        if (body.projectId !== session.identity.projectId || body.runId !== session.identity.runId) {
          return fail(res, 404, "session_not_found", "session does not match project/run");
        }
        const action = { ...args, kind: operation, observationId: body.observationId ?? args.observationId,
          approvalId: body.approvalId ?? args.approvalId } as unknown as BrowserAction;
        const outcome = await executeAction(session, action, { capabilitySecret: opts.capabilitySecret, baselineDir: opts.baselineDir });
        // Observation travels in-process; the wire gets a bounded summary + artifact descriptors only.
        const { observation, ...rest } = outcome;
        return send(res, 200, {
          ...rest,
          artifacts: rest.artifacts.map(({ path: _p, ...a }) => a),
          ...(observation ? { observation: { ...observation, screenshotArtifact: undefined } } : {}),
        });
      }

      if (method === "POST" && parts[2] === "approvals") {
        if (principal.role !== "admin") return fail(res, 403, "forbidden", "only an admin may grant approvals");
        const body = await readJson(req);
        const grant = session.approvals.issue({
          identity: session.identity,
          operation: "click",
          arguments: clickApprovalArguments(body.target as TargetSpec, String(body.pageUrl ?? session.page.url())),
          ttlSeconds: typeof body.ttlSeconds === "number" ? body.ttlSeconds : undefined,
          requestedBy: principal.userId,
        });
        return send(res, 201, grant);
      }

      if (method === "GET" && parts[2] === "har") {
        return send(res, 200, session.diagnostics.toHar());
      }
      if (method === "GET" && parts[2] === "replay-log") {
        return send(res, 200, { entries: session.replayLog });
      }

      if (method === "GET" && parts[2] === "artifacts" && parts[3]) {
        const found = session.artifactIndex.get(parts[3]);
        if (!found?.path) return fail(res, 404, "artifact_not_found", "artifact not found");
        res.writeHead(200, { "content-type": found.mediaType, "x-sha256": found.sha256 });
        return void res.end(await readFile(found.path));
      }
    }
    return fail(res, 404, "not_found", "no such route");
  }
}
