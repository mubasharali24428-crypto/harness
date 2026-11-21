"use client";

// W28: a shell-gate denial asks a person to widen the run's scope. The request
// itself grants nothing; the only write is PUT /runs/{id}/intent and it happens
// ONLY from the editor's Save scope button. Saving scopePaths voids approvals
// and asks again (control.py:_apply), which the editor says in plain words.
import * as React from "react";
import { useEffect, useRef, useState } from "react";
import { ScopeRequestEditor, restoreTriggerFocus } from "@/components/ScopeRequestEditor";
import { scopeEditorPathsText, submitScopeEdit } from "@/lib/scopeRequest";
import { getIntent, putIntent } from "./controlApi";

export function ScopeRequestSection({
  runId,
  token,
  requestedPath,
  onClose,
}: {
  runId: string;
  token: string;
  requestedPath: string;
  onClose: () => void;
}) {
  const [pathsText, setPathsText] = useState("");
  const [version, setVersion] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Keyboard focus: the editor autofocuses its textarea on open. Remember the
  // control that has focus WHILE THIS RENDERS (before that autofocus commits)
  // and hand focus back when the editor unmounts on any close path — Escape,
  // Cancel, or a saved edit. A keyboard user is never dropped to <body>.
  const triggerRef = useRef<HTMLElement | null>(
    typeof document === "undefined" ? null : (document.activeElement as HTMLElement | null),
  );
  useEffect(() => () => restoreTriggerFocus(triggerRef.current), []);

  // Opening the editor reads the current scope; it writes nothing.
  useEffect(() => {
    let cancelled = false;
    void getIntent(runId, token)
      .then((view) => {
        if (cancelled) return;
        setVersion(view.intent.version);
        setPathsText(scopeEditorPathsText(view.intent.scopePaths, requestedPath));
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String((e as Error)?.message ?? e));
      });
    return () => {
      cancelled = true;
    };
  }, [runId, token, requestedPath]);

  const confirm = async () => {
    if (version === null || busy) return;
    setBusy(true);
    setError(null);
    try {
      await submitScopeEdit(putIntent, { runId, token, version, pathsText });
      onClose();
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ScopeRequestEditor
      pathsText={pathsText}
      onPathsTextChange={setPathsText}
      onConfirm={() => void confirm()}
      onCancel={onClose}
      busy={busy || version === null}
      error={error}
    />
  );
}
