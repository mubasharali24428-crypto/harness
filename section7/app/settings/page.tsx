"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import AppShell from "@/components/AppShell";
import { connectGithub, getGithubConnection, type GithubConnection } from "@/lib/api";
import { getToken } from "@/lib/auth";

function SettingsInner() {
  const params = useSearchParams();
  const [conn, setConn] = useState<GithubConnection | null>(null);
  const [error, setError] = useState<string | null>(
    params.get("error") ? `GitHub connect failed (${params.get("error")})` : null,
  );
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    const token = getToken();
    if (!token) return;
    getGithubConnection(token)
      .then(setConn)
      .catch((e: unknown) => setError(String((e as Error)?.message ?? e)));
  }, []);

  useEffect(() => {
    // Clean ?connected=/?error= off the URL so refreshes don't re-show the banner.
    if (params.get("connected") || params.get("error")) {
      window.history.replaceState({}, "", "/settings");
    }
    refresh();
  }, [params, refresh]);

  const onConnect = async () => {
    const token = getToken();
    if (!token) return;
    setBusy(true);
    try {
      await connectGithub(token); // navigates away to github.com
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
      setBusy(false);
    }
  };

  return (
    <AppShell active="/settings">
      <h1>Settings</h1>

      {/* M12-G10: project rules editor (.cursorrules equivalent) lives here */}
      <p style={{ marginBottom: 16 }}>
        <a href="/settings/project" style={{ color: "var(--accent)" }}>Project rules →</a>{" "}
        <span style={{ color: "var(--muted)", fontSize: 12 }}>
          conventions every agent plan must honor
        </span>
      </p>

      {(params.get("connected") || error) && (
        <div
          role="status"
          style={{
            padding: "10px 14px",
            borderRadius: 8,
            marginBottom: 16,
            background: params.get("connected")
              ? "var(--accent-soft)"
              : "rgba(var(--danger-rgb), 0.12)",
            color: params.get("connected") ? "var(--accent)" : "var(--red)",
          }}
        >
          {params.get("connected")
            ? `GitHub connected as ${params.get("connected")}`
            : error}
        </div>
      )}

      <section style={{ maxWidth: 520 }}>
        <h2 style={{ marginTop: 0 }}>Workspace</h2>
        <ul style={{ color: "var(--muted)", fontSize: 14, lineHeight: 2, paddingLeft: 18 }}>
          <li><a href="/settings/team">Team &amp; invites</a></li>
          <li><a href="/settings/governance">Organization governance</a></li>
          <li><a href="/settings/notifications">Notifications (inbox, email, quiet hours)</a></li>
          <li><a href="/settings/models">Models &amp; reasoning</a></li>
          <li><a href="/settings/keys">API keys (BYO providers)</a></li>
          <li><a href="/settings/integrations">Integrations &amp; webhooks</a></li>
          <li><a href="/settings/billing">Billing &amp; plan</a></li>
          <li><a href="/settings/account">Your account (approval rendering, erase)</a></li>
          <li><a href="/onboarding">Onboarding checklist</a></li>
        </ul>
      </section>

      <section style={{ maxWidth: 520 }}>
        <h2 style={{ marginTop: 0 }}>GitHub connection</h2>
        <p style={{ color: "var(--muted)", fontSize: 14 }}>
          Connect your GitHub account so agents can read repos, push branches and
          open draft pull requests on your behalf. Tokens are stored encrypted at
          rest and are never displayed again.
        </p>

        {conn === null ? (
          <p style={{ color: "var(--muted)", fontSize: 14 }}>Loading…</p>
        ) : conn.connected ? (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "12px 14px",
              border: "1px solid var(--border)",
              borderRadius: 8,
            }}
          >
            <span style={{ color: "var(--accent)", fontWeight: 700 }}>Connected</span>
            <span style={{ fontFamily: "ui-monospace, monospace" }}>@{conn.login}</span>
            <span style={{ color: "var(--muted)", fontSize: 13 }}>
              scopes: {conn.scopes.join(", ") || "—"}
            </span>
          </div>
        ) : (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "12px 14px",
              border: "1px solid var(--border)",
              borderRadius: 8,
            }}
          >
            <span style={{ color: "var(--yellow)", fontWeight: 700 }}>Not connected</span>
            <button className="btn" disabled={busy} onClick={onConnect}>
              {busy ? "Redirecting…" : "Connect GitHub"}
            </button>
          </div>
        )}
      </section>
    </AppShell>
  );
}

export default function SettingsPage() {
  return (
    <Suspense fallback={<div style={{ padding: 24, color: "var(--muted)" }}>Loading…</div>}>
      <SettingsInner />
    </Suspense>
  );
}
