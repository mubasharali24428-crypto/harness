import { createServer, type Server } from "node:http";
import { readFile, realpath } from "node:fs/promises";
import { extname, join, resolve, sep } from "node:path";
import type { AddressInfo } from "node:net";
import { classifyUrl } from "./policy.js";
import { WorkerError } from "./types.js";

/**
 * Governed local preview. Private-network origins (loopback etc.) are NEVER
 * accepted from a caller's allowedOrigins directly; they must be registered
 * here for the owning team, as a plain http://127.0.0.1:<port> inside the
 * operator's port range and outside reserved (platform) ports.
 */
export interface PreviewPolicy {
  minPort: number;
  maxPort: number;
  reservedPorts: number[];
}

export class PreviewRegistry {
  private readonly byTeam = new Map<string, Set<string>>();
  constructor(private readonly policy: PreviewPolicy) {}

  register(teamId: string, port: number): string {
    if (!Number.isInteger(port) || port < this.policy.minPort || port > this.policy.maxPort) {
      throw new WorkerError("EGRESS_DENIED", `preview port ${port} outside permitted range`);
    }
    if (this.policy.reservedPorts.includes(port)) {
      throw new WorkerError("EGRESS_DENIED", `preview port ${port} is reserved for platform services`);
    }
    const origin = `http://127.0.0.1:${port}`;
    const set = this.byTeam.get(teamId) ?? new Set<string>();
    set.add(origin);
    this.byTeam.set(teamId, set);
    return origin;
  }

  has(teamId: string, origin: string): boolean {
    return this.byTeam.get(teamId)?.has(origin) ?? false;
  }

  /** Validate a session's origins: private ones need a team registration; public ones an operator allowlist. */
  assertOriginsAllowed(teamId: string, origins: string[], publicAllowlist: string[]): void {
    for (const origin of origins) {
      const cls = classifyUrl(origin);
      if (cls.hasCredentials) throw new WorkerError("EGRESS_DENIED", "credentials in origin");
      if (cls.isPrivateNetwork) {
        if (!this.has(teamId, origin)) {
          throw new WorkerError("EGRESS_DENIED", `private origin '${origin}' is not a registered preview`);
        }
      } else if (!publicAllowlist.includes(origin)) {
        throw new WorkerError("EGRESS_DENIED", `origin '${origin}' is not on the operator allowlist`);
      }
    }
  }
}

const TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json",
};

/** Programmatic static preview of a build directory on loopback; traversal and symlink-escape safe. */
export async function startStaticPreview(dir: string): Promise<{ origin: string; port: number; close: () => Promise<void> }> {
  const root = await realpath(resolve(dir));
  const server: Server = createServer(async (req, res) => {
    try {
      const rel = decodeURIComponent(new URL(req.url ?? "/", "http://x").pathname);
      const target = await realpath(join(root, rel === "/" ? "index.html" : rel));
      if (target !== root && !target.startsWith(root + sep)) throw new Error("escape");
      const body = await readFile(target);
      res.writeHead(200, { "content-type": TYPES[extname(target)] ?? "application/octet-stream" });
      res.end(body);
    } catch {
      res.writeHead(404);
      res.end("not found");
    }
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  const port = (server.address() as AddressInfo).port;
  return { origin: `http://127.0.0.1:${port}`, port, close: () => new Promise((r) => server.close(() => r())) };
}
