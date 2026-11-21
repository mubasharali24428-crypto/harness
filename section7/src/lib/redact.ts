// Secret scrubbing for text shown on screen (timeline receipts, verdict card). A TypeScript port of
// section4/src/harness_orchestrator/redact.py `scrub_secret_patterns`, so the browser masks the
// same shapes the server does. Pattern-based: prefer over-redaction to a leak. Normal identifiers
// (tokenizer=, token_count=) survive.

const PEM = /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g;
// KEY=value / KEY: value for secret-bearing names, with an optional Bearer/Basic/token scheme and
// quoted values that contain spaces. Leading dashes allowed so --token=x and --api-key x match.
const ASSIGN =
  /(\b[A-Za-z0-9_-]*?(?:api[_-]?key|secret|token|password|passwd|authorization|access[_-]?key)\s*[=:]\s*)(?:(?:bearer|basic|token)\s+)?("[^"]*"|'[^']*'|[^\s"']+)/gi;
const BEARER = /\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{6,}/gi;
const SK = /\bsk-[A-Za-z0-9_-]{8,}/g;
const TOKENS =
  /\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|(?:AKIA|ASIA)[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})/g;
// user:password@ in a URL (the password is often a token)
const URL_CREDS = /(\b[a-z][a-z0-9+.-]*:\/\/[^\s:/@]+:)[^\s@/]+@/gi;

export function scrubSecrets(text: string): string {
  return text
    .replace(PEM, "[redacted-private-key]")
    .replace(URL_CREDS, "$1[redacted]@")
    .replace(ASSIGN, "$1[redacted]")
    .replace(BEARER, "$1 [redacted]")
    .replace(SK, "[redacted]")
    .replace(TOKENS, "[redacted]");
}
