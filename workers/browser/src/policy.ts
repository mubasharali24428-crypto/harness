import { WorkerError } from "./types.js";

/**
 * Navigation/egress policy for the browser worker.
 *
 * Default posture: a session may only reach the origins it was opened with.
 * Private-network destinations are additionally flagged so that a loopback
 * "allowed origin" cannot be confused with public reachability, and
 * metadata-style endpoints are denied outright. Redirects are checked after
 * every navigation — an allowed origin redirecting to a disallowed one is the
 * classic SSRF hop.
 */

export interface OriginPolicy {
  allowedOrigins: string[];
}

const PRIVATE_HOST_PATTERNS: RegExp[] = [
  /^localhost$/i,
  /^127\./,
  /^0\.0\.0\.0$/,
  /^10\./,
  /^192\.168\./,
  /^172\.(1[6-9]|2\d|3[01])\./,
  /^169\.254\./,
  /^\[?::1\]?$/,
  /\.local$/i,
  /^metadata\.google\.internal$/i,
];

export interface UrlClassification {
  scheme: string;
  origin: string;
  host: string;
  isPrivateNetwork: boolean;
  hasCredentials: boolean;
}

export function classifyUrl(raw: string): UrlClassification {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new WorkerError("BAD_REQUEST", `unparseable URL: ${raw}`);
  }
  const host = url.hostname;
  return {
    scheme: url.protocol.replace(/:$/, ""),
    origin: url.origin,
    host,
    isPrivateNetwork: PRIVATE_HOST_PATTERNS.some((re) => re.test(host)),
    hasCredentials: url.username !== "" || url.password !== "",
  };
}

export function assertNavigationAllowed(raw: string, policy: OriginPolicy): UrlClassification {
  const cls = classifyUrl(raw);
  if (cls.scheme !== "http" && cls.scheme !== "https") {
    throw new WorkerError(
      "EGRESS_DENIED",
      `scheme '${cls.scheme}:' is not navigable by the browser worker`,
    );
  }
  if (cls.hasCredentials) {
    throw new WorkerError("SSRF_DENIED", "credentials embedded in URL are not allowed");
  }
  if (cls.host === "169.254.169.254" || cls.host === "metadata.google.internal") {
    throw new WorkerError("SSRF_DENIED", `cloud metadata endpoint '${cls.host}' is blocked`);
  }
  if (!policy.allowedOrigins.includes(cls.origin)) {
    const kind = cls.isPrivateNetwork ? "SSRF_DENIED" : "EGRESS_DENIED";
    throw new WorkerError(
      kind,
      `origin '${cls.origin}' is outside this session's allowed origins`,
      { allowedOrigins: policy.allowedOrigins },
    );
  }
  return cls;
}

/** Post-navigation check: covers server-side redirects landing anywhere else. */
export function checkFinalUrl(finalUrl: string, policy: OriginPolicy): void {
  const cls = classifyUrl(finalUrl);
  if (cls.scheme === "about") return; // about:blank after teardown
  if (!policy.allowedOrigins.includes(cls.origin)) {
    throw new WorkerError(
      "SSRF_DENIED",
      `navigation redirected to disallowed origin '${cls.origin}'`,
      { finalUrl, allowedOrigins: policy.allowedOrigins },
    );
  }
}
