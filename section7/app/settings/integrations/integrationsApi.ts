import { ApiError, IDENTITY_BASE, parseApiError } from "@/lib/api";

/** Part 49.003: "slack" posts a one-line chat message instead of the signed JSON envelope. */
export type WebhookPayloadFormat = "harness" | "slack";

export interface WebhookDto {
  id: string;
  endpointUrl: string;
  eventTypes: string[];
  secretHint: string;
  active: boolean;
  version: number;
  createdAt: string;
  payloadFormat?: WebhookPayloadFormat;
}

export interface WebhookDeliveryDto {
  id: string;
  eventId: string;
  eventType: string;
  state: "pending" | "sending" | "delivered" | "failed";
  attempts: number;
  nextAttemptAt: string;
  lastStatusCode: number | null;
  lastError: string | null;
  createdAt: string;
  deliveredAt: string | null;
}

export interface WebhookCreatedDto extends WebhookDto {
  signingSecret: string;
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

const base = (teamId: string) =>
  `/api/v1/teams/${encodeURIComponent(teamId)}/settings/integrations/v1/webhooks`;

export function listWebhookEventTypes(teamId: string, token: string): Promise<{ items: string[] }> {
  return request(`${base(teamId)}/event-types`, token);
}

export function listWebhooks(teamId: string, token: string): Promise<{ items: WebhookDto[] }> {
  return request(base(teamId), token);
}

export function createWebhook(
  teamId: string,
  endpointUrl: string,
  eventTypes: string[],
  token: string,
  payloadFormat: WebhookPayloadFormat = "harness",
): Promise<WebhookCreatedDto> {
  return request(base(teamId), token, {
    method: "POST",
    body: JSON.stringify({ endpointUrl, eventTypes, payloadFormat }),
  });
}

export function setWebhookActive(
  teamId: string,
  webhook: Pick<WebhookDto, "id" | "version">,
  active: boolean,
  token: string,
): Promise<WebhookDto> {
  return request(`${base(teamId)}/${encodeURIComponent(webhook.id)}`, token, {
    method: "PATCH",
    body: JSON.stringify({ active, expectedVersion: webhook.version }),
  });
}

export function rotateWebhookSecret(
  teamId: string,
  webhookId: string,
  token: string,
): Promise<WebhookCreatedDto> {
  return request(`${base(teamId)}/${encodeURIComponent(webhookId)}/rotate-secret`, token, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function listWebhookDeliveries(
  teamId: string,
  webhookId: string,
  token: string,
): Promise<{ items: WebhookDeliveryDto[] }> {
  return request(
    `${base(teamId)}/${encodeURIComponent(webhookId)}/deliveries`,
    token,
  );
}

export function deleteWebhook(teamId: string, webhookId: string, token: string): Promise<null> {
  return request(`${base(teamId)}/${encodeURIComponent(webhookId)}`, token, {
    method: "DELETE",
  }).then(() => null);
}
