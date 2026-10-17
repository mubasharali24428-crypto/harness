// Pure, Electron-free policy for the shell: what may load, navigate, open, or be requested.

export const WEB_PREFERENCES = {
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: true,
  webSecurity: true,
  allowRunningInsecureContent: false,
  webviewTag: false,
  spellcheck: false,
} as const;

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** The origin the shell is allowed to display. https, or http on loopback for local dev. */
export function resolveAppOrigin(raw: string | undefined): URL {
  const url = new URL(raw || "http://localhost:3000");
  if (url.username || url.password) throw new Error("app url must not embed credentials");
  const ok = url.protocol === "https:" || (url.protocol === "http:" && LOOPBACK.has(url.hostname));
  if (!ok) throw new Error("app url must be https (http only for localhost)");
  return url;
}

export function isAllowedNavigation(target: string, appOrigin: URL): boolean {
  try {
    const u = new URL(target);
    return u.origin === appOrigin.origin;
  } catch {
    return false;
  }
}

/** Links that leave the app go to the system browser — https only, never file:/javascript:/custom. */
export function externalOpenAllowed(target: string): boolean {
  try {
    const u = new URL(target);
    return u.protocol === "https:" && !u.username && !u.password;
  } catch {
    return false;
  }
}

// Matched against the RAW string: the URL parser would silently collapse "../" and decode "%2e",
// so a lenient parse could turn a crafted link into a different route.
const DEEP_LINK = /^harness:\/\/(review|run)\/([A-Za-z0-9_-]{1,64})$/;
const ROUTES = { review: "/review", run: "/runs" } as const;

/**
 * harness://review/<changeSetId> and harness://run/<runId> only. Everything else is dropped:
 * a deep link is untrusted input from any process on the machine and never carries credentials.
 */
export function parseDeepLink(raw: string, appOrigin: URL): string | null {
  const m = DEEP_LINK.exec(raw);
  if (!m) return null;
  return new URL(`${ROUTES[m[1] as keyof typeof ROUTES]}/${m[2]}`, appOrigin).toString();
}

export function contentSecurityPolicy(appOrigin: URL): string {
  const o = appOrigin.origin;
  const ws = o.replace(/^http/, "ws");
  return [
    `default-src 'self' ${o}`,
    `connect-src 'self' ${o} ${ws} http://localhost:* https:`,
    `img-src 'self' ${o} data:`,
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
  ].join("; ");
}

/** Every permission request (camera, geolocation, notifications, …) is denied by default. */
export function permissionDecision(_permission: string): boolean {
  return false;
}
