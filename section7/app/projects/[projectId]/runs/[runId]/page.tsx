"use client";

// M14-W4 F3b (W4-C2) — REAL run detail page. Consumes F3a's aggregate read
// (GET /api/v1/orchestrator/runs/{runId}, served by Section 4's orchestrator)
// through getRunAggregate in runsApi.ts. The page renders exactly four things
// and only when the data exists: the state badge (same pill vocabulary as the
// runs index), a cost line at NUMERIC-exact scale-6 money when costUsd is
// non-null (never "$0" fabricated from null), a PR link card when prId/url
// exist, and the step timeline built from the last ≤100 events newest-first
// with human type labels. Honest degradation is the law here: each section
// renders only when its field is present; missing fields render nothing
// rather than a placeholder zero.
//
// The legacy WorkingArea stays mounted below the aggregate header: F1b/F2b
// own its focus/responsive regions this wave, so this lane does not touch it.

import { useCallback, useEffect, useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too, so one source fits both.
import * as React from "react";
import AppShell from "@/components/AppShell";
import { BreadcrumbTrail } from "@/components/Breadcrumbs";
import { WorkingArea } from "@/components/WorkingArea";
import { ChangeReview } from "@/components/ChangeReview";
import { RunTabBar, RunTabPanel, tabFromSearch, type RunTabId } from "@/components/RunTabs";
import { ErrorState, LoadingState } from "@/components/States";
import { ApiError } from "@/lib/api";
import { loadSession } from "@/lib/auth";
// W4 gate repair: the presentational aggregate moved to ./runDetailAggregate
// (Next route modules may export ONLY the default). The page keeps the data
// fetch; rendering helpers live with the component that uses them.
import {
  type RunAggregateDto,
  getRunAggregate,
  pauseOrResumeRun,
  runSettled,
} from "../../../../runs/runsApi";
import { RunDetailAggregate } from "./runDetailAggregate";
import { ScopeRequestSection } from "./scopeRequestSection";
import { SynthesisSection } from "./synthesisPanel";
import { PrReview } from "./prReview";
import { PlanGateCard } from "../../../../dashboard/planGate";
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
  // M19-N4 (C10): the canned demo lands here with ?demo=1. The banner is a
  // UI honesty marker ONLY — the run's own events carry the durable replay
  // marker server-side; this just says it in words on the wow-moment page.
  const [isDemoLanding, setIsDemoLanding] = useState(false);

  const [tab, setTab] = useState<RunTabId>("overview");
  const [stagedPending, setStagedPending] = useState(false);
  const [agg, setAgg] = useState<RunAggregateDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [stale, setStale] = useState(false); // W23: last background refresh failed
  const [notFound, setNotFound] = useState(false);
  // A tool denial names a path; the editor is opened by a person, never by the event.
  const [scopePath, setScopePath] = useState<string | null>(null);

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
    if (typeof window === "undefined") return;
    // Read markers off window so the statically prerendered page never needs
    // a Suspense boundary (same pattern as the login page).
    const search = new URLSearchParams(window.location.search);
    if (search.get("demo") === "1") {
      setIsDemoLanding(true);
    }
    setTab(tabFromSearch(window.location.search));
  }, []);

  const selectTab = useCallback((next: RunTabId) => {
    setTab(next);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.set("tab", next);
      window.history.replaceState(null, "", url.toString());
    }
  }, []);

  const fetchAgg = useCallback(async (quiet = false) => {
    if (!token || !runId) return;
    if (!quiet) {
      setLoading(true);
      setError(null);
      setNotFound(false);
    }
    try {
      setAgg(await getRunAggregate(runId, token));
      setStale(false);
    } catch (e) {
      // A failed background refresh keeps the last good summary on screen,
      // marked as reconnecting (W23) so it is not read as current.
      if (quiet) {
        setStale(true);
        return;
      }
      setAgg(null);
      if (e instanceof ApiError && e.status === 404) {
        setNotFound(true);
      } else {
        setError(String((e as Error)?.message ?? e));
      }
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [token, runId]);

  useEffect(() => {
    void fetchAgg();
  }, [fetchAgg]);

  // Keep the summary live (stop/done change the state after first load).
  // ponytail: 3s polling like the shared panel; push delivery is P4.
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
      await fetchAgg(true);
    } catch (e) {
      setControlNote(`Could not ${action}: ${String((e as Error)?.message ?? e)}`);
    } finally {
      setControlBusy(false);
    }
  }, [token, runId, fetchAgg]);

  const terminal = runSettled(agg);
  useEffect(() => {
    if (!token || !runId || terminal) return;
    const id = setInterval(() => void fetchAgg(true), 3000);
    return () => clearInterval(id);
  }, [fetchAgg, token, runId, terminal]);

  return (
    <AppShell active="/runs">
      {!projectId || !runId ? null : (
        <BreadcrumbTrail
          items={[
            { label: "Command Center", href: "/" },
            { label: "Runs", href: "/runs" },
            { label: `Project ${projectId}`, href: `/projects/${projectId}/tree` },
            { label: `Run ${runId}` },
          ]}
        />
      )}

      {isDemoLanding && (
        // M19-N4 honest marker: this run is REPLAYED RECORDING, not a live
        // execution. Rendered above the fold exactly where the demo lands.
        <div
          role="status"
          data-testid="demo-replay-banner"
          className="pill blue"
          // .pill is nowrap by design; a banner-length sentence must wrap or it
          // forces the whole page to scroll sideways on phones (M-A4).
          style={{ padding: "8px 12px", marginBottom: 16, whiteSpace: "normal", display: "block" }}
        >
          Demo replay — a recorded session, clearly labeled. No provider keys,
          no live execution.
        </div>
      )}

      <RunTabBar active={tab} onChange={selectTab} />

      <RunTabPanel id="overview" active={tab === "overview"}>
        {notFound && (
          <ErrorState
            title="Run not found"
            message="No run with this id is visible to your team. The link may be stale, or the run belongs to another team."
          />
        )}
        {error && !notFound && (
          <ErrorState title="Run didn’t load" message={error} onRetry={() => void fetchAgg()} />
        )}
        {loading && !agg && !notFound && <LoadingState label="Loading run…" rows={4} />}
        {agg && (
          <RunDetailAggregate agg={agg} now={Date.now()} conn={stale ? "reconnecting" : "polling"}
            onPauseResume={(a) => void pauseResume(a)} controlBusy={controlBusy} controlNote={controlNote}
            timelineLive={!terminal} />
        )}
        {/* W28: read-only delegated-children review; reveals itself only for a run with children. */}
        {runId && token && <SynthesisSection runId={runId} token={token} />}
      </RunTabPanel>

      <RunTabPanel id="plan" active={tab === "plan"}>
        {/* Multiplayer pilot: leader, participants, plan gate, direction. */}
        {runId && token && (
          <PlanGateCard token={token} principal={loadSession()?.principal ?? null} runId={runId} />
        )}
        {runId && token && <SessionSixWorkbench runId={runId} token={token} />}
      </RunTabPanel>

      {/* Kept mounted while hidden: it feeds stagedPending into the Working
          controls, which must stay correct even if Team was never opened. */}
      <RunTabPanel id="team" active={tab === "team"} keepMounted>
        {runId && (
          <SharedRunPanel
            runId={runId}
            projectId={projectId ?? undefined}
            onStagedPendingChange={setStagedPending}
          />
        )}
      </RunTabPanel>

      <RunTabPanel id="working" active={tab === "working"}>
        {/* Self-guards on session absence, so it renders nothing without one. */}
        {projectId && runId && (
          <WorkingArea projectId={projectId} runId={runId} stagedPending={stagedPending}
            onScopeRequest={setScopePath} />
        )}
        {scopePath !== null && runId && token && (
          <ScopeRequestSection runId={runId} token={token} requestedPath={scopePath}
            onClose={() => setScopePath(null)} />
        )}
      </RunTabPanel>

      <RunTabPanel id="changes" active={tab === "changes"}>
        {token && agg?.prId && <PrReview prId={agg.prId} token={token} agg={agg} />}
        {projectId && token ? (
          <ChangeReview projectId={projectId} token={token} quietWhenEmpty={Boolean(agg?.prId)} />
        ) : (
          <LoadingState label="Waiting for your session…" rows={2} />
        )}
      </RunTabPanel>
    </AppShell>
  );
}
