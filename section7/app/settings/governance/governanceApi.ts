import { ApiError, IDENTITY_BASE, parseApiError } from "@/lib/api";

export interface OrganizationDto {
  id: string;
  name: string;
  slug: string;
  role: string;
  expiresAt: string | null;
  requireSso?: boolean;
  requireMfa?: boolean;
  requireDualApproval?: boolean;
  eventRetentionDays?: number | null;
  auditRetentionDays?: number | null;
  allowedDomains?: string[];
  executionDisabled?: boolean;
}

export interface OrganizationMemberDto {
  userId: string;
  email: string;
  role: string;
  expiresAt: string | null;
  createdAt: string;
}

export interface PolicyDocumentDto {
  level: string;
  scopeId: string;
  version: number;
  doc: Record<string, unknown>;
}

export interface PolicyChangeRequestDto {
  id: string;
  level: string;
  scopeId: string;
  proposedDoc: Record<string, unknown>;
  expectedVersion: number;
  requestedBy: string;
  requestedAt: string;
}

async function request<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${IDENTITY_BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
      ...(init?.headers ?? {}),
    },
  });
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!response.ok) {
    const { message, code } = parseApiError(response.status, body);
    throw new ApiError(message, code, response.status);
  }
  return body as T;
}

const orgPath = (orgId: string) => `/api/v1/orgs/${encodeURIComponent(orgId)}`;
const policyPath = (orgId: string) => `/api/v1/policy/orgs/${encodeURIComponent(orgId)}`;

export function listOrganizations(token: string): Promise<{ items: OrganizationDto[] }> {
  return request("/api/v1/orgs", token);
}

export function getOrganization(orgId: string, token: string): Promise<OrganizationDto> {
  return request(orgPath(orgId), token);
}

export function getExecutionControl(orgId: string, token: string): Promise<{ orgId: string; disabled: boolean }> {
  return request(`${orgPath(orgId)}/execution-control`, token);
}

export function setExecutionControl(
  orgId: string,
  disabled: boolean,
  reason: string,
  token: string,
): Promise<{ orgId: string; disabled: boolean }> {
  return request(`${orgPath(orgId)}/execution-control`, token, {
    method: "PUT",
    body: JSON.stringify({ disabled, reason }),
  });
}

export function requestStepupCode(email: string, token: string): Promise<{ ok: boolean }> {
  return request("/api/v1/auth/request-login", token, {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function verifyStepupCode(code: string, token: string): Promise<{ verified: boolean }> {
  return request("/api/v1/stepup/verify", token, {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export function listOrganizationMembers(
  orgId: string,
  token: string,
): Promise<{ items: OrganizationMemberDto[] }> {
  return request(`${orgPath(orgId)}/members`, token);
}

export function inviteOrganizationMember(
  orgId: string,
  email: string,
  role: string,
  token: string,
): Promise<Record<string, unknown>> {
  return request(`${orgPath(orgId)}/invites`, token, {
    method: "POST",
    body: JSON.stringify({ email, role }),
  });
}

export function updateOrganizationMember(
  orgId: string,
  userId: string,
  role: string,
  token: string,
): Promise<Record<string, unknown>> {
  return request(`${orgPath(orgId)}/members/${encodeURIComponent(userId)}`, token, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}

export function listPolicyDocuments(
  orgId: string,
  token: string,
): Promise<{ documents: PolicyDocumentDto[] }> {
  return request(`${policyPath(orgId)}/documents`, token);
}

export function previewPolicy(
  orgId: string,
  draft: PolicyDocumentDto,
  userIds: string[],
  token: string,
): Promise<{ results: Array<Record<string, unknown>>; changes: Array<Record<string, unknown>> }> {
  return request(`${policyPath(orgId)}/preview`, token, {
    method: "POST",
    body: JSON.stringify({
      drafts: [{ level: draft.level, scopeId: draft.scopeId, doc: draft.doc }],
      userIds,
      permissions: ["read", "comment", "steer", "approve", "administer", "export", "billing"],
    }),
  });
}

export function savePolicyDocument(
  orgId: string,
  draft: PolicyDocumentDto,
  token: string,
): Promise<{ level: string; scopeId: string; version: number; pendingApproval?: boolean; requestId?: string }> {
  return request(
    `${policyPath(orgId)}/documents/${encodeURIComponent(draft.level)}/${encodeURIComponent(draft.scopeId)}`,
    token,
    {
      method: "PUT",
      body: JSON.stringify({ doc: draft.doc, expectedVersion: draft.version }),
    },
  );
}

export function listPolicyChangeRequests(orgId: string, token: string): Promise<{ requests: PolicyChangeRequestDto[] }> {
  return request(`${policyPath(orgId)}/change-requests`, token);
}

export function decidePolicyChange(
  orgId: string, requestId: string, decision: "approve" | "reject", token: string,
): Promise<{ requestId: string; status: string }> {
  return request(`${policyPath(orgId)}/change-requests/${encodeURIComponent(requestId)}/${decision}`, token, {
    method: "POST",
  });
}

export function patchOrganization(
  orgId: string,
  patch: {
    requireDualApproval?: boolean;
    eventRetentionDays?: number | null;
    auditRetentionDays?: number | null;
  },
  token: string,
): Promise<OrganizationDto> {
  return request(orgPath(orgId), token, { method: "PATCH", body: JSON.stringify(patch) });
}

export function getOrganizationHistory(
  orgId: string,
  token: string,
): Promise<{ items: Array<Record<string, unknown>> }> {
  return request(`${orgPath(orgId)}/history?limit=100`, token);
}

export function getPolicyDecisions(
  orgId: string,
  token: string,
): Promise<{ decisions: Array<Record<string, unknown>> }> {
  return request(`${policyPath(orgId)}/decisions?limit=50`, token);
}

export function listEmergencyGrants(
  orgId: string,
  token: string,
): Promise<{ grants: Array<Record<string, unknown>> }> {
  return request(`${policyPath(orgId)}/emergency`, token);
}

export function requestEmergencyGrant(
  orgId: string,
  input: { permissions: string[]; reason: string; durationMinutes: number },
  token: string,
): Promise<{ id: string; status: string }> {
  return request(`${policyPath(orgId)}/emergency`, token, {
    method: "POST",
    body: JSON.stringify({ ...input, level: "org", scopeId: orgId }),
  });
}

export function decideEmergencyGrant(
  orgId: string,
  grantId: string,
  decision: "approve" | "revoke",
  token: string,
): Promise<Record<string, unknown>> {
  return request(`${policyPath(orgId)}/emergency/${encodeURIComponent(grantId)}/${decision}`, token, {
    method: "POST",
    body: JSON.stringify({}),
  });
}
