"use client";

// Mission 10 node A2 — /settings/keys: BYO provider-keys management page,
// client of contract M10-C1 (Section 1 /api/v1/keys, lands in parallel from A1).
// Add-key form (label/provider/key with show-hide), keys list with hints +
// revoke-with-confirm, minted keyHint shown once. Real fetches against the
// contract shapes; no mocks outside keysApi.ts's single fetch layer.

import { useCallback, useEffect, useRef, useState } from "react";
import AppShell from "@/components/AppShell";
// M13-G5b (raw-14): visible way back — Command Center → Settings → Provider keys.
import { BreadcrumbTrail, settingsCrumbs } from "@/components/Breadcrumbs";
import { loadSession } from "@/lib/auth";
// M13-G5b (raw-14): revoke gains a 5s undo window (countdown cell + helpers).
import {
  REVOKE_UNDO_SECONDS,
  RevokeActionsCell,
} from "./revokeCell";
import {
  KEY_PROVIDERS,
  ApiKeyDto,
  KeyProvider,
  canWriteKeys,
  listKeys,
  mintKey,
  revokeKey,
} from "./keysApi";
// The provider registry lives in the model router (S3 /api/v1/models/catalog);
// the keys page only borrows its names so the picker can offer them.
import { fetchModelCatalog, providerLabel } from "@/lib/modelPrefs";

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
  /** Suggestions only — the mint endpoint accepts any provider string. */
  const [providerChoices, setProviderChoices] = useState<Array<{ id: string; label: string }>>(
    KEY_PROVIDERS.map((p) => ({ id: p, label: p })),
  );
  const [keyInput, setKeyInput] = useState("");
  const [showKey, setShowKey] = useState(false);

  const [confirmRevokeFor, setConfirmRevokeFor] = useState<string | null>(null); // key id
  // M13-G5b (raw-14): the 5s undo window. After Confirm, the DELETE is held
  // back while this counts down to 0; "Keep key" cancels with no request.
  const [undoKey, setUndoKey] = useState<{ keyId: string; label: string; secondsLeft: number } | null>(null);
  const busyRef = useRef(false);
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
    // Registry names for the provider picker. A failure here is not an error
    // state: the fallback list stays and the field still accepts free text.
    fetchModelCatalog(session.token)
      .then((providers) =>
        setProviderChoices(
          providers.map((p) => ({ id: p.name, label: providerLabel(p) })),
        ),
      )
      .catch(() => {});
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
    busyRef.current = true;
    setError(null);
    setConfirmRevokeFor(null);
    try {
      await revokeKey(keyId, token);
      await refresh(teamId); // soft-revoked rows come back stamped revokedAt
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setBusy(false);
      busyRef.current = false;
    }
  };

  // M13-G5b (raw-14): Confirm no longer deletes — it opens a 5s undo window.
  // The DELETE fires only when the countdown expires; "Keep key" cancels the
  // pending revoke with zero network calls made.
  const onConfirmRevoke = (keyId: string, label: string) => {
    setConfirmRevokeFor(null);
    setError(null);
    setNotice(null);
    setMintedHint(null);
    setUndoKey({ keyId, label, secondsLeft: REVOKE_UNDO_SECONDS });
  };

  const onKeepKey = () => {
    setUndoKey(null);
    setNotice("Revoke cancelled — key kept.");
  };

  // Undo-window countdown: one interval while a window is open. At zero the
  // real DELETE runs; if "Keep key" already cleared the window this is a no-op.
  useEffect(() => {
    if (undoKey === null) return;
    const t = window.setInterval(() => {
      setUndoKey((cur) => {
        if (cur === null || cur.secondsLeft <= 0) return cur;
        return { ...cur, secondsLeft: cur.secondsLeft - 1 };
      });
    }, 1000);
    return () => window.clearInterval(t);
  }, [undoKey === null]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (undoKey !== null && undoKey.secondsLeft <= 0 && !busyRef.current) {
      void onRevoke(undoKey.keyId).then(() => {
        // Only clear OUR OWN window: a newer undo (another key) must survive.
        setUndoKey((cur) => (cur !== null && cur.secondsLeft <= 0 ? null : cur));
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [undoKey]);

  return (
    <AppShell active="/settings">
      <BreadcrumbTrail items={settingsCrumbs("Provider keys")} />
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
            {/* A datalist, not a select: the registry holds 50+ providers and
                the mint endpoint accepts any provider string, so a closed list
                would be a policy the backend never set. */}
            <input
              id="key-provider"
              type="text"
              required
              list="provider-registry"
              value={provider}
              onChange={(e) => setProvider(e.target.value.trim())}
              placeholder="openai"
              className="text-input"
              data-testid="key-provider"
              style={{ width: 200 }}
              autoComplete="off"
              spellCheck={false}
            />
            <datalist id="provider-registry">
              {providerChoices.map((choice) => (
                <option key={choice.id} value={choice.id}>
                  {choice.label}
                </option>
              ))}
            </datalist>
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
        {/* M14-F2b (raw-03 P2): overflow-x wrapper + sticky first column (CSS). */}
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Provider keys, scrolls sideways">
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
                      ) : (
                        <RevokeActionsCell
                          label={k.label}
                          keyId={k.id}
                          busy={busy}
                          confirmOpen={confirmRevokeFor === k.id}
                          undoSecondsLeft={undoKey !== null && undoKey.keyId === k.id ? undoKey.secondsLeft : null}
                          onAsk={() => setConfirmRevokeFor(k.id)}
                          onCancelConfirm={() => setConfirmRevokeFor(null)}
                          onConfirm={() => onConfirmRevoke(k.id, k.label)}
                          onKeepKey={onKeepKey}
                        />
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
        </div>
        <p style={{ color: "var(--muted)", fontSize: 12 }}>
          Keys are encrypted at rest; only masked hints are listed. Revoking is soft — the row is
          kept for audit and stops being usable immediately. Confirming starts a{" "}
          {REVOKE_UNDO_SECONDS}-second grace period: “Keep key” cancels before anything is sent.
        </p>
      </section>
    </AppShell>
  );
}
