import type { BrowserContext, Page } from "@playwright/test";

/**
 * Sanitized network + console diagnostics, and subresource egress enforcement.
 *
 * Sanitization is applied at capture time so raw secrets never sit in memory
 * buffers that later get exported. Bodies are never captured. Headers use a
 * deny-by-default allowlist; query strings keep only keys that are not
 * credential-shaped; console text has bearer/JWT/key-looking tokens masked.
 */

const SAFE_HEADERS = new Set(["content-type", "content-length", "cache-control", "accept", "location", "server", "date"]);
const SECRET_KEY_RE = /(token|secret|key|auth|session|sid|password|passwd|pwd|cookie|signature|sig|code|otp|jwt|credential)/i;
const SECRET_TEXT: [RegExp, string][] = [
  [/\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi, "Bearer [REDACTED]"],
  [/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*/g, "[REDACTED_JWT]"],
  [/\b(sk|pk|ghp|gho|xox[abp])[-_][A-Za-z0-9_-]{12,}/g, "[REDACTED_KEY]"],
  [/\b(password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+/gi, "$1=[REDACTED]"],
];

export function sanitizeText(text: string, max = 500): string {
  let out = text;
  for (const [re, rep] of SECRET_TEXT) out = out.replace(re, rep);
  return out.length > max ? `${out.slice(0, max)}…[truncated]` : out;
}

export function sanitizeUrl(raw: string): string {
  try {
    const u = new URL(raw);
    u.username = "";
    u.password = "";
    for (const key of [...u.searchParams.keys()]) {
      if (SECRET_KEY_RE.test(key)) u.searchParams.set(key, "[REDACTED]");
    }
    u.hash = ""; // fragments commonly carry OAuth tokens
    return u.toString();
  } catch {
    return "[unparseable-url]";
  }
}

export function sanitizeHeaders(h: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(h)) {
    const key = k.toLowerCase();
    out[key] = SAFE_HEADERS.has(key) ? sanitizeText(v, 200) : "[REDACTED]";
  }
  return out;
}

export interface NetworkEntry {
  seq: number;
  method: string;
  url: string;
  resourceType: string;
  status: number | null;
  failure?: string;
  blockedByPolicy?: boolean;
  requestHeaders: Record<string, string>;
  responseHeaders: Record<string, string>;
  startedAtMs: number;
  durationMs: number | null;
}

export interface ConsoleEntry {
  seq: number;
  level: string;
  text: string;
  atMs: number;
}

const CAP = 500;

export class Diagnostics {
  readonly network: NetworkEntry[] = [];
  readonly console: ConsoleEntry[] = [];
  private seq = 0;
  private dropped = 0;

  constructor(private readonly now: () => number = () => Date.now()) {}

  private push<T>(arr: T[], item: T): void {
    arr.push(item);
    if (arr.length > CAP) {
      arr.shift();
      this.dropped += 1;
    }
  }

  attach(page: Page): void {
    page.on("console", (msg) =>
      this.push(this.console, { seq: ++this.seq, level: msg.type(), text: sanitizeText(msg.text()), atMs: this.now() }),
    );
    page.on("pageerror", (err) =>
      this.push(this.console, { seq: ++this.seq, level: "pageerror", text: sanitizeText(err.message), atMs: this.now() }),
    );
    const started = new Map<unknown, NetworkEntry>();
    page.on("request", (req) => {
      const entry: NetworkEntry = {
        seq: ++this.seq, method: req.method(), url: sanitizeUrl(req.url()), resourceType: req.resourceType(),
        status: null, requestHeaders: sanitizeHeaders(req.headers()), responseHeaders: {},
        startedAtMs: this.now(), durationMs: null,
      };
      started.set(req, entry);
      this.push(this.network, entry);
    });
    page.on("response", (res) => {
      const e = started.get(res.request());
      if (e) {
        e.status = res.status();
        e.responseHeaders = sanitizeHeaders(res.headers());
        e.durationMs = this.now() - e.startedAtMs;
      }
    });
    page.on("requestfailed", (req) => {
      const e = started.get(req);
      if (e) {
        e.failure = sanitizeText(req.failure()?.errorText ?? "failed", 120);
        if (this.blocked.has(req.url())) e.blockedByPolicy = true;
        e.durationMs = this.now() - e.startedAtMs;
      }
    });
  }

  private readonly blocked = new Set<string>();

  /** Mark a request the egress guard aborted (flags the matching entry once the page reports it). */
  markBlocked(url: string, method: string): void {
    this.blocked.add(url);
    const existing = this.network.find((n) => n.method === method && n.status === null && n.url === sanitizeUrl(url));
    if (existing) existing.blockedByPolicy = true;
  }

  snapshot(opts: { sinceSeq?: number } = {}) {
    const since = opts.sinceSeq ?? 0;
    return {
      network: this.network.filter((n) => n.seq > since),
      console: this.console.filter((c) => c.seq > since),
      dropped: this.dropped,
      lastSeq: this.seq,
    };
  }

  /** HAR 1.2-shaped export (no bodies, no cookies, sanitized headers). */
  toHar() {
    return {
      log: {
        version: "1.2",
        creator: { name: "harness-a3-browser-worker", version: "0.1.0" },
        entries: this.network.map((n) => ({
          startedDateTime: new Date(n.startedAtMs).toISOString(),
          time: n.durationMs ?? 0,
          request: { method: n.method, url: n.url, headers: Object.entries(n.requestHeaders).map(([name, value]) => ({ name, value })), queryString: [], cookies: [], headersSize: -1, bodySize: -1 },
          response: { status: n.status ?? 0, statusText: n.failure ?? "", headers: Object.entries(n.responseHeaders).map(([name, value]) => ({ name, value })), cookies: [], content: { size: -1, mimeType: n.responseHeaders["content-type"] ?? "" }, redirectURL: "", headersSize: -1, bodySize: -1 },
          cache: {}, timings: { send: 0, wait: n.durationMs ?? 0, receive: 0 },
          _blockedByPolicy: n.blockedByPolicy === true,
        })),
      },
    };
  }
}

/**
 * Same-authority egress for subresources: every http(s) request the page makes
 * (fetch/XHR/img/iframe/ws upgrade target) must be to an allowed origin.
 */
export async function installEgressGuard(
  context: BrowserContext,
  allowedOrigins: string[],
  diagnostics: Diagnostics,
): Promise<void> {
  await context.route("**/*", async (route) => {
    const req = route.request();
    let origin: string | null = null;
    let scheme = "";
    try {
      const u = new URL(req.url());
      scheme = u.protocol;
      origin = u.origin;
    } catch {
      /* fall through: abort */
    }
    const local = scheme === "data:" || scheme === "blob:" || scheme === "about:";
    if (local || (origin !== null && allowedOrigins.includes(origin))) return route.continue();
    diagnostics.markBlocked(req.url(), req.method());
    return route.abort("blockedbyclient");
  });
}
