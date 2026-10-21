"use client";

// Part 42 UI: the run's intent (goal, files the agent may touch, permissions) as an editable form.
// The scope paths are enforced by the engine's shell gate, so editing them changes what the agent can do.
import * as React from "react";
import { useCallback, useContext, useEffect, useState } from "react";
import { AnnounceContext } from "@/components/LiveAnnouncer";
import { Button } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { type IntentView, getIntent, putIntent } from "./controlApi";
import { useAction, usePolled } from "./usePolled";

const lines = (v: string): string[] => v.split("\n").map((x) => x.trim()).filter(Boolean);
const text = (v: string[] | null): string => (v ?? []).join("\n");

export function IntentForm(p: {
  view: IntentView; canEdit: boolean; busy: boolean;
  onSave: (patch: { goal: string; scopePaths: string[]; permissions: string[] }) => void;
}) {
  const { intent } = p.view;
  const id = React.useId();
  const [goal, setGoal] = useState(intent.goal ?? "");
  const [scope, setScope] = useState(text(intent.scopePaths));
  const [perms, setPerms] = useState(text(intent.permissions));
  // A newer version from someone else replaces the drafts; the same version keeps what is being typed.
  useEffect(() => { setGoal(intent.goal ?? ""); setScope(text(intent.scopePaths)); setPerms(text(intent.permissions)); }, [intent.version]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section aria-label="Run intent" data-testid="intent-form">
      <h3 style={{ fontSize: 13 }}>Run intent (version {intent.version})</h3>
      <p style={{ color: "var(--muted)" }}>
        The agent can only touch the paths listed here{(intent.scopePaths ?? []).length === 0 ? "; none are listed, so the whole workspace is open" : ""}.
        {p.view.openConflicts > 0 ? ` ${p.view.openConflicts} conflicting edit${p.view.openConflicts === 1 ? "" : "s"} waiting.` : ""}
      </p>
      <form onSubmit={(e) => { e.preventDefault(); p.onSave({ goal, scopePaths: lines(scope), permissions: lines(perms) }); }}>
        <label htmlFor={`${id}-goal`}>Goal</label>
        <textarea id={`${id}-goal`} value={goal} onChange={(e) => setGoal(e.target.value)} disabled={!p.canEdit || p.busy} rows={2} style={{ width: "100%" }} />
        <label htmlFor={`${id}-scope`}>Paths the agent may touch (one per line)</label>
        <textarea id={`${id}-scope`} value={scope} onChange={(e) => setScope(e.target.value)} disabled={!p.canEdit || p.busy} rows={3} style={{ width: "100%" }} />
        <label htmlFor={`${id}-perms`}>Permissions (one per line)</label>
        <textarea id={`${id}-perms`} value={perms} onChange={(e) => setPerms(e.target.value)} disabled={!p.canEdit || p.busy} rows={2} style={{ width: "100%" }} />
        {p.canEdit
          ? <Button size="sm" variant="primary" type="submit" disabled={p.busy}>Save intent</Button>
          : <p style={{ color: "var(--muted)" }}>Your role can only read the intent.</p>}
      </form>
    </section>
  );
}

export function RunIntentSection({ runId, token, tick, canEdit, onChanged }: {
  runId: string; token: string; tick: number; canEdit: boolean; onChanged: () => void;
}) {
  const [view, reload] = usePolled(useCallback(() => getIntent(runId, token), [runId, token]), tick, 15000);
  const after = useCallback(async () => { await reload(); onChanged(); }, [reload, onChanged]);
  const { busy, error, said, run } = useAction(after);
  const announce = useContext(AnnounceContext);
  useEffect(() => { if (announce && said) announce(said); }, [announce, said]);
  if (!view) return null;
  return (
    <div data-testid="run-intent">
      {error && <Notice tone="danger" role="alert">{error}</Notice>}
      <IntentForm view={view} canEdit={canEdit} busy={busy}
        onSave={(patch) => void run(() => putIntent(runId, token, view.intent.version, patch),
          (r) => ((r as { queued?: boolean })?.queued ? "Change queued for the person in control" : "Intent saved"))} />
    </div>
  );
}
