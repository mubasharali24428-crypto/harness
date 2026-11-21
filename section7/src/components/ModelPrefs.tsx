"use client";

// Shared model + reasoning control. One component, two densities:
//   compact — the composer footer (sits next to "⌘↵ to propose")
//   full    — Settings -> Models
// Both read and write the same per-team preference; only admins may change it.

import { listKeys } from "../../app/settings/keys/keysApi";
import * as React from "react";
import { useEffect, useMemo, useState } from "react";

import {
  REASONING_EFFORTS,
  REASONING_LABEL,
  canEditModelPrefs,
  fetchModelCatalog,
  fetchModelPrefs,
  filterProviders,
  providerGroups,
  providerSummary,
  saveModelPrefs,
  scopeNote,
  shortModel,
  type CatalogProvider,
  type ModelPrefs,
  type ReasoningEffort,
} from "@/lib/modelPrefs";

const labelStyle: React.CSSProperties = {
  color: "var(--muted)",
  fontSize: 11,
  letterSpacing: "0.04em",
  textTransform: "uppercase",
};

const selectStyle: React.CSSProperties = {
  background: "var(--panel-2)",
  border: "1px solid var(--border-soft)",
  color: "var(--text)",
  borderRadius: 8,
  padding: "6px 8px",
  fontSize: 12,
  maxWidth: 260,
};

export function ModelPrefsForm({
  token,
  role,
  variant = "full",
}: {
  token: string;
  role?: string | null;
  variant?: "compact" | "full";
}) {
  const [prefs, setPrefs] = useState<ModelPrefs | null>(null);
  const [providers, setProviders] = useState<CatalogProvider[]>([]);
  const [catalogFailed, setCatalogFailed] = useState(false);
  // Providers the team has an active key for. Platform-level keys are not visible here,
  // so the picker only ever says "team key set", never "no key".
  const [keyed, setKeyed] = useState<Set<string>>(() => new Set());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  // The registry lists 50+ providers; a bare <select> of their models is
  // unusable past the first screen, so the full variant filters as you type.
  const [filter, setFilter] = useState("");

  const editable = canEditModelPrefs(role);

  useEffect(() => {
    let cancelled = false;
    fetchModelPrefs(token)
      .then((p) => {
        if (!cancelled) setPrefs(p);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    fetchModelCatalog(token)
      .then((p) => {
        if (!cancelled) setProviders(p);
      })
      .catch(() => {
        if (!cancelled) setCatalogFailed(true);
      });
    listKeys(undefined, token)
      .then((res) => {
        if (!cancelled) setKeyed(new Set(res.items.filter((k) => !k.revokedAt).map((k) => k.provider)));
      })
      .catch(() => {}); // the mark is a hint; the picker works without it
    return () => {
      cancelled = true;
    };
  }, [token]);
  const keyMark = (provider: string) => (keyed.has(provider) ? `${provider} · team key set` : provider);

  const groups = useMemo(
    () =>
      prefs
        ? providerGroups(
            variant === "full" ? filterProviders(providers, filter) : providers,
            [prefs.primaryModel, prefs.reviewerModel],
          )
        : [],
    [providers, prefs, filter, variant],
  );

  const selectedSummary = useMemo(() => {
    if (!prefs) return null;
    const owner = providers.find((p) => p.models.includes(prefs.primaryModel));
    return owner ? providerSummary(owner) : null;
  }, [providers, prefs]);

  function update(patch: Partial<Pick<ModelPrefs, "primaryModel" | "reviewerModel" | "reasoningEffort">>) {
    if (!prefs || !editable) return;
    const previous = prefs;
    const next = { ...prefs, ...patch };
    setPrefs(next); // optimistic; the server response reconciles
    setSaving(true);
    setError(null);
    setSaved(false);
    saveModelPrefs(token, {
      primaryModel: next.primaryModel,
      reviewerModel: next.reviewerModel,
      reasoningEffort: next.reasoningEffort,
    })
      .then((server) => {
        setPrefs(server);
        setSaved(true);
      })
      .catch((e: unknown) => {
        // M-A8: a rejected save used to leave the new value on screen while the
        // status line said "Not saved" — the control lied about the team's
        // active model. Roll the optimistic change back first (same pattern as
        // the roster rows in settings/team).
        setPrefs(previous);
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => setSaving(false));
  }

  if (!prefs) {
    return (
      <span style={{ color: "var(--muted)", fontSize: 11 }} data-testid="model-prefs-loading">
        {error ? `Model settings unavailable: ${error}` : "Loading model settings…"}
      </span>
    );
  }

  const fieldStyle: React.CSSProperties =
    variant === "compact"
      ? { display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }
      : { display: "grid", gap: 6, marginBottom: 18 };

  const modelSelect = (
    value: string,
    onChange: (v: string) => void,
    label: string,
    testId: string,
  ) => (
    <select
      aria-label={label}
      data-testid={testId}
      value={value}
      disabled={!editable || saving}
      onChange={(e) => onChange(e.target.value)}
      style={selectStyle}
    >
      {groups.map((group) => (
        <optgroup key={group.name} label={keyMark(group.name)}>
          {group.models.map((m) => (
            <option key={`${group.name}:${m}`} value={m}>
              {variant === "compact" ? shortModel(m) : m}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );

  const reasoningSelect = (
    <select
      aria-label="Reasoning effort"
      data-testid="reasoning-effort"
      value={prefs.reasoningEffort}
      disabled={!editable || saving}
      onChange={(e) => update({ reasoningEffort: e.target.value as ReasoningEffort })}
      style={{ ...selectStyle, maxWidth: 120 }}
    >
      {REASONING_EFFORTS.map((effort) => (
        <option key={effort} value={effort}>
          {REASONING_LABEL[effort]}
        </option>
      ))}
    </select>
  );

  const status = (
    <span
      role="status"
      style={{ color: error ? "var(--red)" : "var(--muted)", fontSize: 11 }}
      data-testid="model-prefs-status"
    >
      {error
        ? `Not saved: ${error}`
        : saving
          ? "Saving…"
          : saved
            ? "Saved"
            : prefs.persisted
              ? "Team default"
              : "Deployment default"}
    </span>
  );

  if (variant === "compact") {
    // A text box with a datalist is a native searchable picker: type part of a
    // model or provider name and pick. Only an exact catalog model is saved.
    const all = groups.flatMap((g) => g.models.map((m) => ({ model: m, provider: g.name })));
    return (
      <div style={fieldStyle} data-testid="model-prefs-compact">
        <input
          key={prefs.primaryModel}
          list="composer-models"
          aria-label="Run model (type to search)"
          data-testid="composer-model"
          defaultValue={prefs.primaryModel}
          disabled={!editable || saving}
          title={editable ? undefined : "Only team admins can change the model."}
          onChange={(e) => {
            const v = e.target.value;
            if (v !== prefs.primaryModel && all.some((x) => x.model === v)) update({ primaryModel: v });
          }}
          style={selectStyle}
        />
        <datalist id="composer-models">
          {all.map((x) => (
            <option key={`${x.provider}:${x.model}`} value={x.model} label={keyMark(x.provider)} />
          ))}
        </datalist>
        {reasoningSelect}
        {status}
      </div>
    );
  }

  return (
    <div data-testid="model-prefs-form">
      <div style={{ ...fieldStyle, marginBottom: 14 }}>
        <span style={labelStyle}>Filter {providers.length} providers</span>
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="groq, open weights, deepseek…"
          aria-label="Filter providers and models"
          data-testid="model-filter"
          style={{ ...selectStyle, maxWidth: 300 }}
        />
      </div>
      <div style={fieldStyle}>
        <span style={labelStyle}>Primary model (runs, planning)</span>
        {modelSelect(
          prefs.primaryModel,
          (v) => update({ primaryModel: v }),
          "Primary model",
          "primary-model",
        )}
        {selectedSummary && (
          <span style={{ color: "var(--muted)", fontSize: 11 }}>{selectedSummary}</span>
        )}
      </div>
      <div style={fieldStyle}>
        <span style={labelStyle}>Reviewer model</span>
        {modelSelect(
          prefs.reviewerModel,
          (v) => update({ reviewerModel: v }),
          "Reviewer model",
          "reviewer-model",
        )}
      </div>
      <div style={fieldStyle}>
        <span style={labelStyle}>Reasoning effort</span>
        {reasoningSelect}
      </div>
      <p style={{ color: "var(--muted)", fontSize: 12, margin: "0 0 10px", maxWidth: 620 }}>
        {scopeNote(prefs.reasoningWireSupported)}
        {!editable && " Only team admins can change these."}
      </p>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>{status}</div>
      {catalogFailed && (
        <p style={{ color: "var(--warn)", fontSize: 12, marginTop: 10 }}>
          The model catalog could not be loaded; only the current choices are shown.
        </p>
      )}
    </div>
  );
}
