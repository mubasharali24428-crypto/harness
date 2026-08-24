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
// W4 gate repair: presentational editor moved to ./rulesEditor — Next route
// modules may export ONLY the default; tests import it from there directly.
import { RulesEditor } from "./rulesEditor";

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
