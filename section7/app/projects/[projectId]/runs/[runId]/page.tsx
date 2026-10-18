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
import { ApiError } from "@/lib/api";
import { loadSession } from "@/lib/auth";
// W4 gate repair: the presentational aggregate moved to ./runDetailAggregate
// (Next route modules may export ONLY the default). The page keeps the data
// fetch; rendering helpers live with the component that uses them.
import {
  type RunAggregateDto,
  getRunAggregate,
} from "../../../../runs/runsApi";
import { RunDetailAggregate } from "./runDetailAggregate";
import { PlanGateCard } from "../../../../dashboard/planGate";
import { SharedRunPanel } from "./sharedRunPanel";

const mutedStyle = { color: "var(--muted)" } as const;

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

  const [stagedPending, setStagedPending] = useState(true);
  const [agg, setAgg] = useState<RunAggregateDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);

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
    // Read the marker off window so the statically prerendered page never
    // needs a Suspense boundary (same pattern as the login page).
    if (
      typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).get("demo") === "1"
    ) {
      setIsDemoLanding(true);
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
    } catch (e) {
      // A failed background refresh keeps the last good summary on screen.
      if (quiet) return;
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
  const terminal = /^(done|stopped|failed|completed|cancelled)$/i.test(agg?.state ?? "");
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
          style={{ padding: "8px 12px", marginBottom: 16 }}
        >
          Demo replay — a recorded session, clearly labeled. No provider keys,
          no live execution.
        </div>
      )}
      {notFound && (
        <div role="alert" className="pill red" style={{ padding: "8px 12px", marginBottom: 16 }}>
          Run not found (or not visible to your team).
        </div>
      )}
      {error && !notFound && (
        <div role="alert" className="pill red" style={{ padding: "8px 12px", marginBottom: 16 }}>
          {error}
        </div>
      )}
      {loading && !agg && !notFound && (
        <p style={mutedStyle} aria-live="polite">
          Loading run…
        </p>
      )}

      {agg && <RunDetailAggregate agg={agg} now={Date.now()} />}

      {/* Multiplayer pilot: leader, participants, pending decisions, direction. */}
      {runId && token && <PlanGateCard token={token} principal={loadSession()?.principal ?? null} runId={runId} />}
      {runId && <SharedRunPanel runId={runId} onStagedPendingChange={setStagedPending} />}

      {/* Legacy working area (F1b/F2b territory this wave — untouched). It
          self-guards on session absence, so it renders nothing until the
          browser holds a session, same as before this lane existed. */}
      {projectId && runId && (
        <WorkingArea projectId={projectId} runId={runId} stagedPending={stagedPending} />
      )}
    </AppShell>
  );
}
