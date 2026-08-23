// Wave 9 node C — team/invites API client for the /settings/team page.
// Lives next to the page (node C's file boundary); talks to Section 1's
// identity service with the same bearer/error conventions as src/lib/api.ts.
import { ApiError, IDENTITY_BASE, parseApiError } from "@/lib/api";

export type TeamRole = "admin" | "engineer" | "viewer" | "guest";
export const TEAM_ROLES: TeamRole[] = ["admin", "engineer", "viewer", "guest"];

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
  createdAt: string;
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

export function listPendingInvites(
  teamId: string,
  token: string,
): Promise<{ items: TeamInviteDto[] }> {
  return request(`/api/v1/teams/${teamId}/invites`, token);
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

export function revokeInvite(
  teamId: string,
  inviteId: string,
  token: string,
): Promise<{ ok: boolean }> {
  return request(`/api/v1/teams/${teamId}/invites/${inviteId}`, token, { method: "DELETE" });
}

export function redeemInvite(
  inviteToken: string,
  sessionToken: string,
): Promise<{ membership: { teamId: string; role: string }; alreadyMember: boolean }> {
  return request(`/api/v1/invites/redeem`, sessionToken, {
    method: "POST",
    body: JSON.stringify({ token: inviteToken }),
  });
}
