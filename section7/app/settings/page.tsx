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

      {(params.get("connected") || error) && (
        <div
          role="status"
          style={{
            padding: "10px 14px",
            borderRadius: 8,
            marginBottom: 16,
            background: params.get("connected") ? "#0f2a1e" : "#2a1215",
            color: params.get("connected") ? "#34d399" : "#f87171",
          }}
        >
          {params.get("connected")
            ? `GitHub connected as ${params.get("connected")}`
            : error}
        </div>
      )}

      <section style={{ maxWidth: 520 }}>
        <h2 style={{ marginTop: 0 }}>GitHub connection</h2>
        <p style={{ color: "#9aa4b2", fontSize: 14 }}>
          Connect your GitHub account so agents can read repos, push branches and
          open draft pull requests on your behalf. Tokens are stored encrypted at
          rest and are never displayed again.
        </p>

        {conn === null ? (
          <p style={{ color: "#6b7280", fontSize: 14 }}>Loading…</p>
        ) : conn.connected ? (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "12px 14px",
              border: "1px solid #1f2937",
              borderRadius: 8,
            }}
          >
            <span style={{ color: "#34d399", fontWeight: 700 }}>Connected</span>
            <span style={{ fontFamily: "ui-monospace, monospace" }}>@{conn.login}</span>
            <span style={{ color: "#6b7280", fontSize: 13 }}>
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
              border: "1px solid #1f2937",
              borderRadius: 8,
            }}
          >
            <span style={{ color: "#f59e0b", fontWeight: 700 }}>Not connected</span>
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
    <Suspense fallback={<div style={{ padding: 24, color: "#6b7280" }}>Loading…</div>}>
      <SettingsInner />
    </Suspense>
  );
}
