// F-gap closure (docs/runbooks/external-validation.md §SSO): the SPA half of
// the OIDC flow. Section 1 binds redirect_uri per login attempt and echoes it
// at complete ("state does not match this login attempt" otherwise) — so the
// EXACT string used at start is stashed here and replayed verbatim by the
// callback page. No secrets are involved: state/PKCE hashes live server-side
// in sso_login_state; the stash holds only teamId + redirectUri.
export const SSO_PENDING_KEY = "harness.sso.pending";
export const SSO_CALLBACK_PATH = "/auth/sso/callback";

export interface PendingSso {
  teamId: string;
  redirectUri: string;
}

/** The redirect_uri this SPA binds at start and must echo at complete. */
export function ssoRedirectUri(origin: string): string {
  return `${origin}${SSO_CALLBACK_PATH}`;
}

export function savePendingSso(storage: Storage, pending: PendingSso): void {
  storage.setItem(SSO_PENDING_KEY, JSON.stringify(pending));
}

export function readPendingSso(storage: Storage): PendingSso | null {
  const raw = storage.getItem(SSO_PENDING_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as PendingSso;
    if (
      typeof parsed?.teamId === "string" &&
      typeof parsed?.redirectUri === "string"
    ) {
      return parsed;
    }
    return null;
  } catch {
    return null;
  }
}

export function clearPendingSso(storage: Storage): void {
  storage.removeItem(SSO_PENDING_KEY);
}

export interface SsoCallbackParams {
  code: string | null;
  state: string | null;
  error: string | null;
}

/** IdPs redirect back with either code+state or an error/hint pair. */
export function readSsoCallbackParams(search: string): SsoCallbackParams {
  const params = new URLSearchParams(search);
  return {
    code: params.get("code"),
    state: params.get("state"),
    error: params.get("error"),
  };
}
