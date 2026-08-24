"use client";

// M12-G10 — /settings/project: user-authored project rules editor panel
// (the harness's .cursorrules equivalent; part3 gap #9 first slice).
//
//   • textarea editor + live char counter (chars + UTF-8 bytes against the
//     64KB server cap — the counter disables Save before a predictable 413);
//   • Save (PUT, admin/engineer only) and Clear (DELETE → back to "");
//   • role gating mirrors the roster page: admin/engineer see an editable
//     panel, viewer/guest get a read-only view (the server enforces too);
//   • failures surface the SERVER's error message verbatim via parseApiError
//     (e.g. 413 RULES_TOO_LARGE text) — never a fabricated excuse.
//
// The rules are advisory planner context (S4 injects a "PROJECT RULES
// (user-authored)" block at plan-proposal time); they never override the
// sandbox allow-list or forbidden_paths. The page says so.

import { useCallback, useEffect, useState } from "react";
import * as React from "react";
import AppShell from "@/components/AppShell";
import { getProjectId, loadSession } from "@/lib/auth";
import {
  MAX_RULES_BYTES,
  ProjectRulesDto,
  deleteProjectRules,
  getProjectRules,
  rulesByteLength,
  saveProjectRules,
} from "./projectRulesApi";

const cardStyle = { maxWidth: 760 } as const;
const textareaStyle = {
  width: "100%",
  minHeight: 220,
  background: "var(--panel-2)",
  border: "1px solid var(--border)",
  color: "var(--text)",
  borderRadius: 6,
  padding: "8px 10px",
  font: "13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace",
  resize: "vertical",
} as const;

/** Pure presentational panel — exported for tests, SSR-safe. */
export function RulesEditor({
  projectId,
  initial,
  canEdit,
  busy,
  onTextChange,
  onSave,
  onClear,
}: {
  projectId: string;
  initial: ProjectRulesDto | null;
  canEdit: boolean;
  /** True while any save/clear round-trip is in flight. */
  busy: boolean;
  onTextChange: (text: string) => void;
  onSave: () => void;
  onClear: () => void;
}) {
  const [text, setText] = useState(initial?.rulesText ?? "");
  const chars = text.length;
  const bytes = rulesByteLength(text);
  const overCap = bytes > MAX_RULES_BYTES;

  return (
    <section className="card" style={cardStyle}>
      <h2>Project rules</h2>
      <p style={{ color: "var(--muted)", fontSize: 12 }}>
        Conventions every agent plan must honor — like <code>.cursorrules</code>{" "}
        for this project. Advisory: agents are guided by these rules, but hard
        limits (sandbox allow-list, forbidden paths) still apply regardless.
      </p>
      {!canEdit && (
        <p style={{ color: "var(--muted)", fontSize: 12 }}>
          Editing is restricted to team admins and engineers.
        </p>
      )}
      <textarea
        data-testid="rules-textarea"
        aria-label="Project rules"
        value={text}
        readOnly={!canEdit}
        disabled={busy && canEdit}
        onChange={(e) => {
          setText(e.target.value);
          onTextChange(e.target.value);
        }}
        placeholder={"e.g.\nAlways use pnpm, never npm.\nNo default exports outside app/."}
        style={{ ...textareaStyle, opacity: canEdit ? 1 : 0.75 }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8 }}>
        {/* Char + byte counter; turns red over the server cap */}
        <span
          data-testid="rules-counter"
          role="status"
          style={{
            fontSize: 12,
            color:
              bytes > MAX_RULES_BYTES
                ? "var(--red)"
                : chars > MAX_RULES_BYTES * 0.9
                  ? "var(--yellow)"
                  : "var(--muted)",
          }}
        >
          {chars.toLocaleString()} chars · {bytes.toLocaleString()} /{" "}
          {(MAX_RULES_BYTES / 1024).toFixed(0)} KB
        </span>
        {overCap && (
          <span role="alert" style={{ fontSize: 12, color: "var(--red)" }}>
            Over the {MAX_RULES_BYTES / 1024} KB limit.
          </span>
        )}
        {canEdit && (
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8 }}>
            <button
              type="button"
              className="btn danger"
              data-testid="rules-clear"
              disabled={busy || (!initial?.rulesText && !text)}
              onClick={() => {
                setText("");
                onClear();
              }}
            >
              Clear
            </button>
            <button
              type="button"
              className="btn primary"
              data-testid="rules-save"
              disabled={busy || overCap}
              onClick={() => onSave()}
            >
              {busy ? "Saving…" : "Save rules"}
            </button>
          </span>
        )}
      </div>
      {initial?.updatedAt && (
        <p style={{ color: "var(--muted)", fontSize: 11, marginTop: 6 }}>
          Last updated {new Date(initial.updatedAt).toLocaleString()}
          {initial.updatedBy ? ` · by ${initial.updatedBy}` : ""}
        </p>
      )}
    </section>
  );
}

export default function ProjectSettingsPage() {
  const [projectId, setProjectId] = useState<string | null>(null);
  const [doc, setDoc] = useState<ProjectRulesDto | null>(null);
  const [draftText, setDraftText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Same gating rule as the roster page: frozen Principal role decides who
  // may write. Read post-mount so SSR and first client render agree.
  const [role, setRole] = useState<string>("");
  useEffect(() => {
    setRole(loadSession()?.principal?.role ?? "");
  }, []);
  const canEdit = role === "admin" || role === "engineer";

  const refresh = useCallback(async (pid: string, token: string) => {
    const fresh = await getProjectRules(pid, token);
    setDoc(fresh);
    setDraftText(null);
  }, []);

  useEffect(() => {
    const session = loadSession();
    if (!session) {
      setError("Not signed in — log in first.");
      return;
    }
    const pid = getProjectId();
    if (!pid) {
      setError("Pick a project in the sidebar first.");
      return;
    }
    setProjectId(pid);
    refresh(pid, session.token).catch((e: unknown) =>
      setError(String((e as Error)?.message ?? e)),
    );
  }, [refresh]);

  const onSave = async () => {
    if (!projectId || draftText === null) return;
    const token = loadSession()?.token ?? "";
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await saveProjectRules(projectId, draftText, token);
      setDoc(saved);
      setDraftText(null);
      setNotice(
        saved.rulesText
          ? "Rules saved — agents will honor them when proposing plans."
          : "Rules cleared.",
      );
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const onClear = async () => {
    if (!projectId || draftText === null) return;
    const token = loadSession()?.token ?? "";
    if (draftText !== "") {
      // Textarea was emptied manually — that IS the clear: save the empty doc.
      await onSave();
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await deleteProjectRules(projectId, token);
      await refresh(projectId, token);
      setNotice("Rules deleted.");
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell active="/settings">
      <h1>Project settings</h1>

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

      {!projectId ? null : (
        <RulesEditor
          projectId={projectId}
          initial={doc}
          canEdit={canEdit}
          busy={busy}
          onTextChange={(t) => setDraftText(t)}
          onSave={() => {
            void onSave();
          }}
          onClear={() => {
            void onClear();
          }}
        />
      )}
    </AppShell>
  );
}
