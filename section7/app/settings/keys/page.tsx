"use client";

// Mission 10 node A2 — /settings/keys: BYO provider-keys management page,
// client of contract M10-C1 (Section 1 /api/v1/keys, lands in parallel from A1).
// Add-key form (label/provider/key with show-hide), keys list with hints +
// revoke-with-confirm, minted keyHint shown once. Real fetches against the
// contract shapes; no mocks outside keysApi.ts's single fetch layer.

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { loadSession } from "@/lib/auth";
import {
  KEY_PROVIDERS,
  ApiKeyDto,
  KeyProvider,
  canWriteKeys,
  listKeys,
  mintKey,
  revokeKey,
} from "./keysApi";

const cardStyle = { maxWidth: 760, marginBottom: 20 } as const;
const tableStyle = { width: "100%", borderCollapse: "collapse", fontSize: 13 } as const;
const thTdStyle = {
  textAlign: "left",
  padding: "6px 8px",
  borderBottom: "1px solid var(--border)",
} as const;

const selectStyle = {
  background: "var(--panel-2)",
  border: "1px solid var(--border)",
  color: "var(--text)",
  borderRadius: 6,
  padding: "6px 8px",
} as const;

const providerPillClass: Record<string, string> = {
  openai: "pill green",
  anthropic: "pill yellow",
  deepseek: "pill blue",
};

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function KeysSettingsPage() {
  const [teamId, setTeamId] = useState<string | null>(null);
  const [role, setRole] = useState<string | undefined>(undefined);
  const [keys, setKeys] = useState<ApiKeyDto[]>([]);
  const [loaded, setLoaded] = useState(false);

  // add-key form
  const [label, setLabel] = useState("");
  const [provider, setProvider] = useState<KeyProvider>("openai");
  const [keyInput, setKeyInput] = useState("");
  const [showKey, setShowKey] = useState(false);

  const [confirmRevokeFor, setConfirmRevokeFor] = useState<string | null>(null); // key id
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  /** Minted hint shown once — cleared when the next add or revoke starts. */
  const [mintedHint, setMintedHint] = useState<string | null>(null);

  const canWrite = canWriteKeys(role);

  const refresh = useCallback(async (team: string) => {
    const token = loadSession()?.token ?? "";
    const res = await listKeys(team, token);
    setKeys(res.items);
    setLoaded(true);
  }, []);

  useEffect(() => {
    const session = loadSession();
    if (!session) {
      setError("Not signed in — log in first.");
      return;
    }
    setTeamId(session.principal.teamId);
    setRole(session.principal.role);
    refresh(session.principal.teamId).catch((e: unknown) =>
      setError(String((e as Error)?.message ?? e)),
    );
  }, [refresh]);

  const onAddSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!teamId || !canWrite) return;
    const token = loadSession()?.token ?? "";
    setBusy(true);
    setError(null);
    setNotice(null);
    setMintedHint(null);
    try {
      const minted = await mintKey(
        { label: label.trim(), provider, key: keyInput },
        token,
      );
      setMintedHint(minted.keyHint); // hint shown exactly once
      setNotice(`Key "${minted.label}" added (${minted.provider}).`);
      setLabel("");
      setKeyInput("");
      setShowKey(false);
      await refresh(teamId);
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const onRevoke = async (keyId: string) => {
    if (!teamId) return;
    const token = loadSession()?.token ?? "";
    setBusy(true);
    setError(null);
    setConfirmRevokeFor(null);
    try {
      await revokeKey(keyId, token);
      await refresh(teamId); // soft-revoked rows come back stamped revokedAt
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell active="/settings">
      <h1>Provider keys</h1>

      {!canWrite && (
        <p style={{ color: "var(--muted)", fontSize: 12 }}>
          Your role is read-only — you can view keys but not add or revoke them.
        </p>
      )}

      {error && (
        <div role="alert" className="pill red" style={{ padding: "8px 12px", marginBottom: 16 }}>
          {error}
        </div>
      )}
      {!error && notice && (
        <div role="status" style={{ color: "var(--green)", marginBottom: 16 }}>
          {notice}
        </div>
      )}

      {canWrite && (
        <section className="card" style={cardStyle}>
          <h2>Add a provider key</h2>
          <form onSubmit={onAddSubmit} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <label htmlFor="key-label" style={{ alignSelf: "center" }}>
              Label
            </label>
            <input
              id="key-label"
              type="text"
              required
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="prod openai"
              className="text-input"
              style={{ width: 180 }}
            />
            <label htmlFor="key-provider" style={{ alignSelf: "center" }}>
              Provider
            </label>
            <select
              id="key-provider"
              value={provider}
              onChange={(e) => setProvider(e.target.value as KeyProvider)}
              style={selectStyle}
            >
              {KEY_PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            <label htmlFor="key-input" style={{ alignSelf: "center" }}>
              Key
            </label>
            <input
              id="key-input"
              type={showKey ? "text" : "password"}
              required
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              placeholder="sk-…"
              className="text-input"
              style={{ flex: 1, minWidth: 200 }}
              autoComplete="off"
            />
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => setShowKey((v) => !v)}
              aria-pressed={showKey}
              aria-label={showKey ? "Hide key" : "Show key"}
            >
              {showKey ? "Hide" : "Show"}
            </button>
            <button type="submit" className="btn primary" disabled={busy}>
              Add key
            </button>
          </form>

          {mintedHint !== null && (
            <div
              data-testid="minted-hint"
              role="status"
              style={{
                marginTop: 12,
                padding: "10px 12px",
                border: "1px solid var(--yellow)",
                borderRadius: 8,
              }}
            >
              <strong style={{ color: "var(--yellow)" }}>Key stored.</strong> The raw key is
              encrypted and never shown again — only this hint identifies it:
              <br />
              <code style={{ wordBreak: "break-all" }}>{mintedHint}</code>
            </div>
          )}
        </section>
      )}

      <section className="card" style={cardStyle}>
        <h2>Team keys</h2>
        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={thTdStyle}>Label</th>
              <th style={thTdStyle}>Provider</th>
              <th style={thTdStyle}>Key hint</th>
              <th style={thTdStyle}>Created</th>
              <th style={thTdStyle}>Status</th>
              {canWrite && <th style={thTdStyle}>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => {
              const revoked = k.revokedAt !== null && k.revokedAt !== undefined;
              return (
                <tr key={k.id}>
                  <td style={thTdStyle}>{k.label}</td>
                  <td style={thTdStyle}>
                    <span className={providerPillClass[k.provider] ?? "pill blue"}>
                      {k.provider}
                    </span>
                  </td>
                  <td style={thTdStyle}>
                    <code>{k.keyHint}</code>
                  </td>
                  <td style={thTdStyle}>{fmtDate(k.createdAt)}</td>
                  <td style={thTdStyle}>
                    {revoked ? (
                      <span className="pill red">revoked</span>
                    ) : (
                      <span className="pill green">active</span>
                    )}
                  </td>
                  {canWrite && (
                    <td style={thTdStyle}>
                      {revoked ? (
                        "—"
                      ) : confirmRevokeFor === k.id ? (
                        <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                          Revoke “{k.label}”?
                          <button
                            type="button"
                            className="btn danger"
                            disabled={busy}
                            onClick={() => onRevoke(k.id)}
                            aria-label={`Confirm revoking key ${k.label}`}
                          >
                            Confirm
                          </button>
                          <button
                            type="button"
                            className="btn"
                            disabled={busy}
                            onClick={() => setConfirmRevokeFor(null)}
                          >
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button
                          type="button"
                          className="btn danger"
                          disabled={busy}
                          onClick={() => setConfirmRevokeFor(k.id)}
                          aria-label={`Revoke key ${k.label}`}
                        >
                          Revoke
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
            {keys.length === 0 && (
              <tr>
                <td style={thTdStyle} colSpan={canWrite ? 6 : 5}>
                  {loaded ? "No provider keys yet." : "Loading…"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <p style={{ color: "var(--muted)", fontSize: 12 }}>
          Keys are encrypted at rest; only masked hints are listed. Revoking is soft — the row is
          kept for audit and stops being usable immediately.
        </p>
      </section>
    </AppShell>
  );
}
