// Wave 9 node C — team/invites API client for the /settings/team page.
// Lives next to the page (node C's file boundary); talks to Section 1's
// identity service with the same bearer/error conventions as src/lib/api.ts.
//
// M12-E3b — extended for H-010 (roster management) under pair contract
// W2-C1: DELETE /api/v1/teams/{id}/members/{userId} → 204 and
// PATCH .../members/{userId} {role} → 200 {userId, role}; errors come back
// as error_body() and surface through parseApiError unchanged.
//
// M14-F2a — invite lifecycle: the list endpoint now returns EVERY status
// (draft/sent/redeemed/expired/revoked derived server-side), resend re-mints
// the token (old one revoked server-side in the same transaction) and revoke
// stamps instead of deleting. Statuses arrive as strings; render them as-is —
// never guess a status client-side.
import { ApiError, IDENTITY_BASE, parseApiError } from "@/lib/api";

export type TeamRole = "admin" | "engineer" | "viewer" | "guest";
export const TEAM_ROLES: TeamRole[] = ["admin", "engineer", "viewer", "guest"];

/** M14-F2a — lifecycle statuses exactly as Section 1 derives them. */
export const INVITE_STATUSES = [
  "draft",
  "sent",
  "redeemed",
  "expired",
  "revoked",
] as const;
export type InviteStatus = (typeof INVITE_STATUSES)[number];

export interface TeamMemberDto {
  id: string;
  displayName: string;
  email: string | null;
  memberType: string;
  role: string;
  createdAt: string;
}

export interface TeamInviteDto {
  id: string;
  teamId: string;
  email: string;
  role: string;
  expiresAt: string;
  redeemedAt: string | null;
  /** M14-F2a: stamped when an admin revokes (row is kept, not deleted). */
  revokedAt: string | null;
  createdAt: string;
  /** M14-F2a: server-derived lifecycle status — render it, never re-derive. */
  status: string;
}

export interface MintedInviteDto extends TeamInviteDto {
  /** Raw token — returned exactly once by the mint endpoint, never again. */
  token: string;
}

async function request<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${IDENTITY_BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
      ...(init?.headers ?? {}),
    },
  });
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!res.ok) {
    const { message, code } = parseApiError(res.status, body);
    throw new ApiError(message, code, res.status);
  }
  return body as T;
}

export function listMembers(teamId: string, token: string): Promise<{ items: TeamMemberDto[] }> {
  return request(`/api/v1/teams/${teamId}/members`, token);
}

/** W2-C1 — DELETE /api/v1/teams/{teamId}/members/{userId} → 204.
 *  Admin-only server-side; audited as member.removed by E3a. The 204 carries
 *  no body, so this resolves to null. UNVERIFIED until E3a lands. */
export async function removeMember(
  teamId: string,
  userId: string,
  token: string,
): Promise<null> {
  await request(`/api/v1/teams/${teamId}/members/${userId}`, token, {
    method: "DELETE",
  });
  return null;
}

/** W2-C1 — PATCH /api/v1/teams/{teamId}/members/{userId} {role} →
 *  200 {userId, role}. Admin-only server-side; audited as
 *  member.role_changed by E3a. UNVERIFIED until E3a lands. */
export function updateMemberRole(
  teamId: string,
  userId: string,
  role: TeamRole,
  token: string,
): Promise<{ userId: string; role: string }> {
  return request(`/api/v1/teams/${teamId}/members/${userId}`, token, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}

/** M14-F2a — full lifecycle list (every status). Admin-only server-side;
 *  non-admin callers get 403/404 and the page degrades to the roster only. */
export function listInvites(
  teamId: string,
  token: string,
): Promise<{ items: TeamInviteDto[] }> {
  return request(`/api/v1/teams/${teamId}/invites`, token);
}

/** Back-compat alias for the old pending-only read: ?status=sent narrows to
 *  live invites exactly like the wave-9 endpoint did. */
export function listPendingInvites(
  teamId: string,
  token: string,
): Promise<{ items: TeamInviteDto[] }> {
  return request(`/api/v1/teams/${teamId}/invites?status=sent`, token);
}

export function createInvite(
  teamId: string,
  email: string,
  role: TeamRole,
  token: string,
): Promise<MintedInviteDto> {
  return request(`/api/v1/teams/${teamId}/invites`, token, {
    method: "POST",
    body: JSON.stringify({ email, role }),
  });
}

/** M14-F2a — POST .../invites/{id}/resend → 201 {…invite, token}.
 *  Server mints a fresh token and kills the old one in one transaction. */
export function resendInvite(
  teamId: string,
  inviteId: string,
  token: string,
): Promise<MintedInviteDto> {
  return request(`/api/v1/teams/${teamId}/invites/${inviteId}/resend`, token, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

/** M14-F2a — revoke now stamps revoked_at; the row stays visible as "revoked". */
export function revokeInvite(
  teamId: string,
  inviteId: string,
  token: string,
): Promise<{ ok: boolean }> {
  return request(`/api/v1/teams/${teamId}/invites/${inviteId}`, token, { method: "DELETE" });
}

// M18-C5: the server binds redemption to the invited email; a token alone answers 422.
export function redeemInvite(
  inviteToken: string,
  email: string,
  sessionToken: string,
): Promise<{ membership: { teamId: string; role: string }; alreadyMember: boolean }> {
  return request(`/api/v1/invites/redeem`, sessionToken, {
    method: "POST",
    body: JSON.stringify({ token: inviteToken, email }),
  });
}
