"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { BreadcrumbTrail } from "@/components/Breadcrumbs";
import { getToken, loadSession } from "@/lib/auth";
import {
  WebhookDeliveryDto,
  WebhookDto,
  createWebhook,
  deleteWebhook,
  listWebhookDeliveries,
  listWebhookEventTypes,
  listWebhooks,
  rotateWebhookSecret,
  setWebhookActive,
} from "./integrationsApi";

export default function IntegrationsSettingsPage() {
  const session = loadSession();
  const token = getToken();
  const teamId = session?.principal.teamId ?? "";
  const isAdmin = session?.principal.role === "admin";
  const [webhooks, setWebhooks] = useState<WebhookDto[]>([]);
  const [eventTypes, setEventTypes] = useState<string[]>([]);
  const [selectedEvents, setSelectedEvents] = useState<string[]>([]);
  const [endpointUrl, setEndpointUrl] = useState("");
  const [deliveries, setDeliveries] = useState<Record<string, WebhookDeliveryDto[]>>({});
  const [oneTimeSecret, setOneTimeSecret] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!token || !teamId || !isAdmin) return;
    const [hooks, events] = await Promise.all([
      listWebhooks(teamId, token),
      listWebhookEventTypes(teamId, token),
    ]);
    setWebhooks(hooks.items);
    setEventTypes(events.items);
  }, [isAdmin, teamId, token]);

  useEffect(() => {
    refresh().catch((cause: unknown) => setError(String((cause as Error)?.message ?? cause)));
  }, [refresh]);

  const onCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token || !teamId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    setOneTimeSecret(null);
    try {
      const created = await createWebhook(teamId, endpointUrl.trim(), selectedEvents, token);
      setOneTimeSecret(created.signingSecret);
      setEndpointUrl("");
      setSelectedEvents([]);
      setNotice("Webhook created. Copy the signing secret now; it will not be shown again.");
      await refresh();
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    } finally {
      setBusy(false);
    }
  };

  const onToggle = async (webhook: WebhookDto) => {
    if (!token || !teamId) return;
    setError(null);
    try {
      await setWebhookActive(teamId, webhook, !webhook.active, token);
      await refresh();
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  const onRotate = async (webhookId: string) => {
    if (!token || !teamId) return;
    setError(null);
    setOneTimeSecret(null);
    try {
      const result = await rotateWebhookSecret(teamId, webhookId, token);
      setOneTimeSecret(result.signingSecret);
      setNotice("Signing secret rotated. Copy the new secret now; the old one no longer works.");
      await refresh();
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  const onDelete = async (webhookId: string) => {
    if (!token || !teamId || !window.confirm("Delete this webhook and its delivery history?")) return;
    setError(null);
    try {
      await deleteWebhook(teamId, webhookId, token);
      setNotice("Webhook deleted.");
      await refresh();
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  const onShowDeliveries = async (webhookId: string) => {
    if (!token || !teamId) return;
    setError(null);
    try {
      const response = await listWebhookDeliveries(teamId, webhookId, token);
      setDeliveries((current) => ({ ...current, [webhookId]: response.items }));
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  return (
    <AppShell active="/settings">
      <BreadcrumbTrail items={[{ label: "Settings", href: "/settings" }, { label: "Integrations" }]} />
      <h1>Integrations</h1>
      {!session && <p role="alert">Sign in to manage integrations.</p>}
      {session && !isAdmin && (
        <p role="status">Only a team admin can view or change integration endpoints.</p>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {oneTimeSecret && (
        <section aria-label="New signing secret" className="card" style={{ maxWidth: 760 }}>
          <h2>Copy this signing secret</h2>
          <p>Harness will not show it again. Store it in your endpoint’s secret manager.</p>
          <code data-testid="signing-secret" style={{ overflowWrap: "anywhere" }}>
            {oneTimeSecret}
          </code>
          <button type="button" onClick={() => setOneTimeSecret(null)}>
            Hide secret
          </button>
        </section>
      )}

      {session && isAdmin && (
        <>
          <section className="card" style={{ maxWidth: 760, marginBottom: 20 }}>
            <h2>Create outbound webhook</h2>
            <p>
              Destinations must use HTTPS and a host on the operator allow-list. Payloads contain
              event IDs and resource identity only. Deliveries are signed and retried up to six
              times.
            </p>
            <form onSubmit={onCreate}>
              <label htmlFor="webhook-url">HTTPS endpoint URL</label>
              <input
                id="webhook-url"
                type="url"
                required
                value={endpointUrl}
                onChange={(event) => setEndpointUrl(event.target.value)}
                autoComplete="url"
              />
              <fieldset>
                <legend>Events to send</legend>
                {eventTypes.map((eventType) => (
                  <label key={eventType} style={{ display: "block" }}>
                    <input
                      type="checkbox"
                      checked={selectedEvents.includes(eventType)}
                      onChange={(event) =>
                        setSelectedEvents((current) =>
                          event.target.checked
                            ? [...current, eventType]
                            : current.filter((item) => item !== eventType),
                        )
                      }
                    />
                    {eventType}
                  </label>
                ))}
              </fieldset>
              <button type="submit" disabled={busy || selectedEvents.length === 0}>
                {busy ? "Creating…" : "Create webhook"}
              </button>
            </form>
          </section>

          <section aria-labelledby="webhooks-heading" style={{ maxWidth: 900 }}>
            <h2 id="webhooks-heading">Configured webhooks</h2>
            {webhooks.length === 0 ? <p>No webhooks configured.</p> : null}
            {webhooks.map((webhook) => (
              <article className="card" key={webhook.id} style={{ marginBottom: 12 }}>
                <h3>{webhook.endpointUrl}</h3>
                <p>
                  Status: <strong>{webhook.active ? "Active" : "Paused"}</strong>. Signing key hint: {webhook.secretHint}.
                </p>
                <p>Events: {webhook.eventTypes.join(", ")}</p>
                <button type="button" onClick={() => onToggle(webhook)}>
                  {webhook.active ? "Pause" : "Resume"}
                </button>{" "}
                <button type="button" onClick={() => onRotate(webhook.id)}>
                  Rotate signing secret
                </button>{" "}
                <button type="button" onClick={() => onShowDeliveries(webhook.id)}>
                  Refresh delivery health
                </button>{" "}
                <button type="button" onClick={() => onDelete(webhook.id)}>
                  Delete
                </button>
                {deliveries[webhook.id] && (
                  <ul aria-label={`Recent deliveries for ${webhook.endpointUrl}`}>
                    {deliveries[webhook.id].length === 0 && <li>No deliveries yet.</li>}
                    {deliveries[webhook.id].map((delivery) => (
                      <li key={delivery.id}>
                        {delivery.eventType}: {delivery.state}; attempt {delivery.attempts}; HTTP{" "}
                        {delivery.lastStatusCode ?? "pending"}
                        {delivery.lastError ? ` (${delivery.lastError})` : ""}
                      </li>
                    ))}
                  </ul>
                )}
              </article>
            ))}
          </section>
        </>
      )}
    </AppShell>
  );
}
