// Mission 10 node A2 — BYO provider-keys API client for /settings/keys.
// Client side of contract M10-C1 (/api/v1/keys, bearer auth): the backend lands
// in parallel from A1, so this file codes against the CONTRACT shapes only.
// Lives next to the page (A2's file boundary); same bearer/error conventions as
// app/settings/team/teamApi.ts and src/lib/api.ts.
import { ApiError, IDENTITY_BASE, parseApiError } from "@/lib/api";

/** Providers accepted by the mint endpoint (contract: `{label, provider, key}`). */
export type KeyProvider = "openai" | "anthropic" | "deepseek" | "ollama";
export const KEY_PROVIDERS: KeyProvider[] = ["openai", "anthropic", "deepseek", "ollama"];

/** One row of GET /api/v1/keys — camelCase per contract M10-C1. */
export interface ApiKeyDto {
  id: string;
  label: string;
  provider: string;
  /** Masked hint, e.g. `sk-…abcd`. The raw key NEVER comes back over the wire. */
  keyHint: string;
  createdAt: string;
  /** Soft-revoke timestamp; null while the key is still usable. */
  revokedAt: string | null;
}

/** 201 body of POST /api/v1/keys — the only time a key's identity is revealed,
 *  and even then just the hint (raw key is encrypted at rest, never returned). */
export interface MintedKeyDto {
  id: string;
  label: string;
  provider: string;
  keyHint: string;
  createdAt: string;
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
      body = null; // e.g. 204 No Content from DELETE
    }
  }
  if (!res.ok) {
    const { message, code } = parseApiError(res.status, body);
    throw new ApiError(message, code, res.status);
  }
  return body as T;
}

/** Mint a key: POST /api/v1/keys → 201 MintedKeyDto (raw key never returned again). */
export function mintKey(
  input: { label: string; provider: KeyProvider; key: string },
  token: string,
): Promise<MintedKeyDto> {
  return request("/api/v1/keys", token, {
    method: "POST",
    body: JSON.stringify({ label: input.label, provider: input.provider, key: input.key }),
  });
}

/** List the team's keys: GET /api/v1/keys?teamId= → {items: ApiKeyDto[]}. */
export function listKeys(teamId: string | undefined, token: string): Promise<{ items: ApiKeyDto[] }> {
  const qs = teamId ? `?teamId=${encodeURIComponent(teamId)}` : "";
  return request(`/api/v1/keys${qs}`, token);
}

/** Soft-revoke: DELETE /api/v1/keys/{id} → 204 (backend stamps revoked_at). */
export async function revokeKey(keyId: string, token: string): Promise<{ ok: boolean }> {
  await request(`/api/v1/keys/${encodeURIComponent(keyId)}`, token, { method: "DELETE" });
  return { ok: true };
}

/** Mirror of harness_contracts.roles.can(principal, "write"): viewer/guest are
 *  read-only. UI convenience only — the API re-enforces this server-side. */
export function canWriteKeys(role: string | undefined): boolean {
  return role === "admin" || role === "engineer";
}
