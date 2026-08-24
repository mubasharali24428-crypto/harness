// M12-G10 — user-authored project rules client for /settings/project.
// Talks to Section 1's identity service with the same bearer/error
// conventions as teamApi.ts. Server contract (harness_identity.rules_api):
//   GET    /api/v1/projects/{projectId}/rules → 200 {projectId, rulesText,
//          updatedAt, updatedBy} — rulesText "" when none was ever saved
//   PUT    .../rules {rulesText} → 200 same projection; over-cap → 413
//          RULES_TOO_LARGE; viewer/guest → 403 FORBIDDEN
//   DELETE .../rules → 204 (idempotent)
import { ApiError, IDENTITY_BASE, parseApiError } from "@/lib/api";

export const MAX_RULES_BYTES = 64 * 1024;

export interface ProjectRulesDto {
  projectId: string;
  rulesText: string;
  updatedAt: string | null;
  updatedBy: string | null;
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

/** Load the project's rules document ("" when none configured). */
export function getProjectRules(
  projectId: string,
  token: string,
): Promise<ProjectRulesDto> {
  return request(`/api/v1/projects/${projectId}/rules`, token);
}

/** Upsert the single rules document for the project (admin/engineer). */
export function saveProjectRules(
  projectId: string,
  rulesText: string,
  token: string,
): Promise<ProjectRulesDto> {
  return request(`/api/v1/projects/${projectId}/rules`, token, {
    method: "PUT",
    body: JSON.stringify({ rulesText }),
  });
}

/** Remove the document entirely ("no rules configured"). Idempotent 204. */
export async function deleteProjectRules(
  projectId: string,
  token: string,
): Promise<null> {
  await request(`/api/v1/projects/${projectId}/rules`, token, { method: "DELETE" });
  return null;
}

/** Client-side mirror of the server's UTF-8 byte cap so the Save button and
 *  counter tell the truth BEFORE a predictable 413 round-trip. */
export function rulesByteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}
