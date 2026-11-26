"use client";

// R2 run workspace: the run is the centre column (title, state, spend, step
// blocks, verdict) and the right pane shows one thing at a time — Diff, Plan,
// Terminal, Evidence or Advanced. The five old tabs are gone; their content
// moved into the panes unchanged (same components, same API calls), and old
// ?tab= links open the matching pane. The pane lives in ?pane= so reload,
// shared links and back/forward land on the same view.

import { useCallback, useEffect, useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too, so one source fits both.
import * as React from "react";
import AppShell from "@/components/AppShell";
import { WorkingArea } from "@/components/WorkingArea";
import { ChangeReview } from "@/components/ChangeReview";
import { RunColumn } from "@/components/workspace/RunColumn";
import { ReviewPane } from "@/components/workspace/ReviewPane";
import { RunEvidence } from "@/components/workspace/RunEvidence";
import { PresenceAvatars } from "@/components/workspace/PresenceAvatars";
import { useRun } from "@/components/workspace/useRun";
import { Composer, COMPOSER_INPUT_ID } from "@/components/workspace/Composer";
import { PlanPin } from "@/components/workspace/PlanPin";
import { InlineApprovals, NextStepBox } from "@/components/workspace/RunPrompts";
import { LearnedRuleOffer } from "@/components/workspace/LearnedRuleOffer";
import { runRowTitle } from "@/components/workspace/runRows";
import { PANES, paneFromSearch, searchWithPane, type PaneId } from "@/components/workspace/panes";
import { showChangeReview, type DiffScope } from "@/lib/diffScope";
import { formatMoneyUsd, formatRelativeTime } from "@/lib/format";
import { ErrorState, LoadingState } from "@/components/States";
import { Button } from "@/components/ui";
import { loadSession } from "@/lib/auth";
// W4 gate repair: the presentational aggregate moved to ./runDetailAggregate
// (Next route modules may export ONLY the default). The page keeps the data
// fetch; rendering helpers live with the component that uses them.
import { pauseOrResumeRun, runSettled, stateLabel } from "../../../../runs/runsApi";
import { RunDetailAggregate } from "./runDetailAggregate";
import { ScopeRequestSection } from "./scopeRequestSection";
import { SynthesisSection } from "./synthesisPanel";
import { SharedRunPanel } from "./sharedRunPanel";
import { SessionSixWorkbench } from "./sessionSixWorkbench";

export default function RunDetailPage({
  params,
}: {
  params: Promise<{ projectId: string; runId: string }>;
}) {
  const [projectId, setProjectId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [selfId, setSelfId] = useState<string | null>(null);
  // M19-N4 (C10): the canned demo lands here with ?demo=1. The banner is a
  // UI honesty marker ONLY — the run's own events carry the durable replay
  // marker server-side.
  const [isDemoLanding, setIsDemoLanding] = useState(false);
  const [pane, setPane] = useState<PaneId | null>(null);
  const [stagedPending, setStagedPending] = useState(false);
  // A tool denial names a path; the editor is opened by a person, never by the event.
  const [scopePath, setScopePath] = useState<string | null>(null);
  // R4: the Diff pane's scope. While since-last is primary (or still being
  // decided), the snapshot history below it would repeat the same hunks.
  const [diffScope, setDiffScope] = useState<DiffScope | null>(null);
  useEffect(() => setDiffScope(null), [runId]);

  // Next 15 hands route params as a Promise; accept both shapes so tests can
  // construct the component without awaiting a fake Promise.
  React.useEffect(() => {
    let cancelled = false;
    void Promise.resolve(params).then((p) => {
      if (cancelled) return;
      setProjectId(p.projectId);
      setRunId(p.runId);
    });
    return () => {
      cancelled = true;
    };
  }, [params]);

  useEffect(() => {
    setToken(loadSession()?.token ?? null);
    setRole(loadSession()?.principal.role ?? null);
    setSelfId(loadSession()?.principal.userId ?? null);
    // Read markers off window so the statically prerendered page never needs
    // a Suspense boundary (same pattern as the login page).
    const read = () => {
      setIsDemoLanding(new URLSearchParams(window.location.search).get("demo") === "1");
      setPane(paneFromSearch(window.location.search));
    };
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, []);

  const selectPane = useCallback((next: PaneId | null) => {
    setPane(next);
    window.history.pushState(null, "", window.location.pathname + searchWithPane(window.location.search, next));
  }, []);

  const view = useRun(runId, token);
  const { agg } = view;

  const [controlBusy, setControlBusy] = useState(false);
  const [controlNote, setControlNote] = useState<string | null>(null);
  const pauseResume = useCallback(async (action: "pause" | "resume") => {
    if (!token || !runId) return;
    setControlBusy(true);
    try {
      const r = await pauseOrResumeRun(runId, token, action);
      setControlNote(action === "resume"
        ? "Resumed: the agent continues from where it stopped, with any direction sent while paused."
        : r.status === "paused"
          ? "Paused: the agent's process is stopped; it cannot change files until you resume."
          : "Pausing: the agent may still finish the step it is on.");
      await view.refresh();
    } catch (e) {
      setControlNote(`Could not ${action}: ${String((e as Error)?.message ?? e)}`);
    } finally {
      setControlBusy(false);
    }
  }, [token, runId, view.refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  const terminal = runSettled(agg);
  const control = agg?.state === "executing" ? "pause" : agg?.state === "paused" ? "resume" : null;
  const cost = formatMoneyUsd(agg?.costUsd ?? null);
  const estimate = formatMoneyUsd(agg?.estimatedCostUsd ?? null);

  return (
    <AppShell active="/runs" runId={runId}>
      <div className={`ws-run${pane ? " with-pane" : ""}`}>
        <div className="ws-centre">
          {isDemoLanding && (
            // M19-N4 honest marker: this run is REPLAYED RECORDING, not a live execution.
            <p role="status" data-testid="demo-replay-banner" className="ws-replay">
              REPLAY · a recorded session, clearly labeled. No provider keys, no live execution.
            </p>
          )}

          {agg && (
            <header className="ws-run-head">
              <div className="ws-run-head-row">
                <span className={`ws-dot lg ${agg.state}`} aria-hidden="true" />
                <h1 className="ws-run-title">{runRowTitle(view.plan?.goal)}</h1>
                {control && (
                  <Button size="sm" variant={control === "pause" ? undefined : "primary"} disabled={controlBusy}
                    onClick={() => void pauseResume(control)}>
                    {control === "pause" ? "Pause" : "Resume"}
                  </Button>
                )}
                {runId && token && <PresenceAvatars runId={runId} token={token} onOpen={() => selectPane("advanced")} />}
                {agg.url ? (
                  <a className="ws-run-pr" href={agg.url} target="_blank" rel="noreferrer">
                    Pull request ↗
                  </a>
                ) : null}
              </div>
              <p className="ws-run-meta">
                <span>{stateLabel(agg.state)}</span>
                <span aria-hidden="true">·</span>
                <span title="Metered spend for this run (cost ledger)">
                  Spend {cost === "unknown" ? <span className="ws-unknown">unknown</span> : cost}
                </span>
                <span aria-hidden="true">·</span>
                <span title="The plan's own estimate, not actual spend">
                  Estimated {estimate === "unknown" ? <span className="ws-unknown">unknown</span> : estimate}
                </span>
                <span aria-hidden="true">·</span>
                <span>Started {formatRelativeTime(agg.steps[0]?.ts ?? null)}</span>
              </p>
              {controlNote && <p role="status" className="ws-conn-note">{controlNote}</p>}
              {view.stale && (
                <p role="status" className="ws-conn-note">
                  Reconnecting: this is the last state the server reported.
                </p>
              )}
            </header>
          )}

          <div className="ws-pane-tabs" role="group" aria-label="Open in the side pane">
            {PANES.map((p) => (
              <button key={p.id} type="button" className="ws-pane-tab" aria-pressed={pane === p.id}
                onClick={() => selectPane(pane === p.id ? null : p.id)}>
                {p.label}
              </button>
            ))}
          </div>

          {view.status === "notFound" ? (
            <ErrorState
              title="Run not found"
              message="No run with this id is visible to your team. The link may be stale, or the run belongs to another team."
              onRetry={view.retry}
            />
          ) : view.status === "error" ? (
            <ErrorState title="Run didn’t load" message={view.error ?? "Something went wrong."} onRetry={view.retry} />
          ) : view.status === "loading" ? (
            <LoadingState label="Loading run…" rows={4} />
          ) : (
            <RunColumn view={view} hideVerdict={pane === "diff"} />
          )}

          {agg && runId && token && (
            <>
              <InlineApprovals runId={runId} token={token} role={role} selfId={selfId} tick={agg} />
              {/* I-08: a repeated-denial stop offers its lesson as a rule to copy; never saved here. */}
              <LearnedRuleOffer runId={runId} token={token} steps={agg.steps} />
              <NextStepBox state={agg.state} busy={controlBusy}
                onAction={(a) => {
                  if (a === "diff") selectPane("diff");
                  else if (a === "resume") void pauseResume("resume");
                  else document.getElementById(COMPOSER_INPUT_ID)?.focus();
                }} />
              <div className="ws-dock">
                <PlanPin runId={runId} token={token} role={role} plan={view.plan} state={agg.state} onChanged={() => void view.refresh()} />
                <Composer runId={runId} projectId={projectId} token={token} role={role} state={agg.state}
                  onChanged={() => void view.refresh()} />
              </div>
            </>
          )}
        </div>

        {/* Always mounted: the Advanced section feeds stagedPending into the
            Terminal controls, which must be right even if it was never opened. */}
        <aside className="ws-pane" hidden={!pane} aria-label={PANES.find((p) => p.id === pane)?.label ?? "Side pane"}>
          <div className="ws-pane-head">
            <h2 className="ws-pane-title">{PANES.find((p) => p.id === pane)?.label}</h2>
            <button type="button" className="ws-pane-close" aria-label="Close the pane" onClick={() => selectPane(null)}>
              ✕
            </button>
          </div>

          {pane === "diff" && (
            <>
              {agg && token && (
                <ReviewPane view={view} token={token} role={role} projectId={projectId} runKey={runId}
                  onDiffScopeChange={setDiffScope} />
              )}
              {agg?.prId && (
                <a className="ws-foot-link" href={`/review?pr=${encodeURIComponent(agg.prId)}`}>
                  Approve or request changes →
                </a>
              )}
              {projectId && token && showChangeReview(pane, diffScope) && (
                <ChangeReview projectId={projectId} token={token} quietWhenEmpty={Boolean(agg?.prId)} />
              )}
            </>
          )}

          {pane === "plan" && runId && token && (
            // Approve, Reject, Edit and Start live on the pinned plan above the composer.
            <SessionSixWorkbench runId={runId} token={token} />
          )}

          {pane === "terminal" && projectId && runId && (
            <>
              <WorkingArea projectId={projectId} runId={runId} stagedPending={stagedPending}
                onScopeRequest={setScopePath} />
              {scopePath !== null && token && (
                <ScopeRequestSection runId={runId} token={token} requestedPath={scopePath}
                  onClose={() => setScopePath(null)} />
              )}
            </>
          )}

          {pane === "evidence" && runId && (
            <>
              {agg && token && <RunEvidence agg={agg} token={token} />}
              {agg && (
                <RunDetailAggregate agg={agg} now={Date.now()} conn={view.stale ? "reconnecting" : "polling"}
                  timelineLive={!terminal} />
              )}
              {/* W28: read-only delegated-children review; reveals itself only for a run with children. */}
              {token && <SynthesisSection runId={runId} token={token} />}
              {projectId && (
                <p className="ws-evidence-links">
                  <a className="ws-foot-link" href={`/projects/${projectId}/runs/${runId}/report`}>Read-only report →</a>
                  <a className="ws-foot-link" href={`/projects/${projectId}/runs/${runId}/checkpoints`}>Checkpoints →</a>
                  <a className="ws-foot-link" href={`/projects/${projectId}/tree`}>Repository files →</a>
                  <a className="ws-foot-link" href={`/projects/${projectId}/planning`}>Assignment planning →</a>
                </p>
              )}
            </>
          )}

          <div hidden={pane !== "advanced"}>
            {runId && view.status === "ready" && (
              <SharedRunPanel
                runId={runId}
                projectId={projectId ?? undefined}
                onStagedPendingChange={setStagedPending}
              />
            )}
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
