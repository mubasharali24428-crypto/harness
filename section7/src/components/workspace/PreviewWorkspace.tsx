"use client";

// R1 preview workspace (docs/briefs/ui-master-prompt §SECTION R1): the three
// screens the owner judges the new LOOK on. One shell, three panes:
//   sidebar — run list grouped Needs you / Working / Done, per project
//   centre  — the run as one scrolling column of step blocks
//   pane    — one thing at a time: Diff (with the verdict card above it on
//             /preview/review)
// Real data only, via usePreviewRun. States designed: loading (skeleton),
// empty (says what to do next, with the action), error (plain sentence +
// retry), unknown (words, never fabricated). No grid background, no window
// buttons, no clock, no breadcrumbs, no banner stack.

import * as React from "react";
import Link from "next/link";
import { Button } from "@/components/ui";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { formatMoneyUsd, formatRelativeTime, formatTimestamp } from "@/lib/format";
import { stateLabel } from "../../../app/runs/runsApi";
import type { RunDto } from "../../../app/runs/runsApi";
import { useEventStream } from "@/lib/useEventStream";
import { loadSession } from "@/lib/auth";
import { usePreviewRun, type PreviewView } from "./usePreviewRun";
import { runRowState, runRowTitle } from "./previewData";
import { RunColumn } from "./RunColumn";
import { DiffPane } from "./DiffPane";
import { ReviewPane } from "./ReviewPane";
import { SignedOutState } from "./SignedOutState";

export type PreviewPane = "none" | "diff" | "review";

const GROUPS: Array<{ key: keyof PreviewView["groups"]; label: string; empty: string }> = [
  { key: "needsYou", label: "Needs you", empty: "Nothing is waiting on a person." },
  { key: "working", label: "Working", empty: "No agent is working right now." },
  { key: "done", label: "Done", empty: "No finished runs yet." },
];

function RunRow({
  run,
  active,
  goal,
}: {
  run: RunDto;
  active: boolean;
  goal: string | null;
}) {
  const when = run.startedAt ?? run.createdAt;
  return (
    <li>
      <Link
        href={`/preview/run?run=${encodeURIComponent(run.id)}`}
        className={`ws-row${active ? " active" : ""}`}
        title={goal ?? undefined}
      >
        <span className="ws-row-title">{runRowTitle(goal)}</span>
        <span className="ws-row-meta">
          <span className={`ws-dot ${run.status}`} aria-hidden="true" />
          {runRowState(run)}
          <span aria-hidden="true">·</span>
          <time dateTime={when} title={formatTimestamp(when)}>
            {formatRelativeTime(when)}
          </time>
        </span>
      </Link>
    </li>
  );
}

function Sidebar({ view, activeRunId, goals }: { view: PreviewView; activeRunId: string | null; goals: Record<string, string> }) {
  return (
    <aside className="ws-sidebar" aria-label="Runs">
      <div className="ws-sidebar-brand">
        <span className="ws-brand-mark" aria-hidden="true" />
        Harness
      </div>
      <div className="ws-switcher">
        <label className="ws-label" htmlFor="ws-project">
          Project
        </label>
        <select
          id="ws-project"
          value={view.selectedProjectId ?? ""}
          onChange={(e) => view.selectProject(e.target.value)}
        >
          {view.projects.length === 0 && <option value="">(no projects yet)</option>}
          {view.projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <nav className="ws-groups" aria-label="Run list">
        {GROUPS.map((g) => {
          const items = view.groups[g.key];
          return (
            <section key={g.key} className="ws-group" aria-labelledby={`ws-group-${g.key}`}>
              <h2 id={`ws-group-${g.key}`} className="ws-group-label">
                {g.label}
                <span className="ws-group-count" aria-hidden="true">
                  {items.length}
                </span>
              </h2>
              {items.length === 0 ? (
                <p className="ws-group-empty">{g.empty}</p>
              ) : (
                <ul className="ws-rows">
                  {items.map((run) => (
                    <RunRow
                      key={run.id}
                      run={run}
                      active={run.id === activeRunId}
                      goal={goals[run.id] ?? null}

                    />
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </nav>
      <div className="ws-sidebar-foot">
        <Link href="/" className="ws-foot-link">
          Old workspace
        </Link>
        <Link href="/preview/review" className="ws-foot-link">
          Review
        </Link>
      </div>
    </aside>
  );
}

function CentreHeader({ view }: { view: PreviewView }) {
  const { agg, plan, runId } = view;
  if (!agg || !runId) return null;
  const cost = formatMoneyUsd(agg.costUsd);
  const estimate = formatMoneyUsd(agg.estimatedCostUsd);
  return (
    <header className="ws-run-head">
      <div className="ws-run-head-row">
        <span className={`ws-dot lg ${agg.state}`} aria-hidden="true" />
        <h1 className="ws-run-title">{runRowTitle(plan?.goal)}</h1>
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
    </header>
  );
}

function CentreBody({ view, hideVerdict }: { view: PreviewView; hideVerdict: boolean }) {
  if (view.status === "loading") {
    return (
      <div className="ws-centre-loading">
        <LoadingState label="Loading the run…" rows={4} />
      </div>
    );
  }
  if (view.status === "empty") {
    return (
      <EmptyState
        title="No runs yet"
        body="Start the demo to see a completed run with its diff and review verdict — no API keys needed."
        action={
          <Button variant="primary" onClick={() => void view.seedDemoRun()} disabled={view.seeding}>
            {view.seeding ? "Preparing the demo run…" : "Load the demo run"}
          </Button>
        }
      />
    );
  }
  if (view.status === "notFound") {
    return (
      <ErrorState
        title="Run not found"
        message="No run with this id is visible to your team. The link may be stale, or the run belongs to another team."
        onRetry={view.retry}
      />
    );
  }
  if (view.status === "error") {
    return <ErrorState title="The run didn’t load" message={view.error ?? "Something went wrong."} onRetry={view.retry} />;
  }
  if (view.status === "signedOut") {
    return <SignedOutState />;
  }
  if (view.seedError) {
    return <ErrorState title="The demo didn’t start" message={view.seedError} onRetry={view.retry} />;
  }
  return <RunColumn view={view} hideVerdict={hideVerdict} />;
}

/**
 * The three-pane workspace shell. `pane` picks the right side ("none" = the
 * bare run screen); each route renders the same shell with a different pane.
 */
export default function PreviewWorkspace({ pane, deepLinkRunId }: { pane: PreviewPane; deepLinkRunId: string | null }) {
  const view = usePreviewRun(deepLinkRunId);
  const showPane = pane !== "none" && view.status === "ready";
  const session = typeof window !== "undefined" ? loadSession() : null;
  const projectId = view.agg ? view.selectedProjectId : null;
  const { events, connectionState } = useEventStream(projectId, session?.token ?? null);
  const goals = React.useMemo(() => {
    const map: Record<string, string> = {};
    if (view.plan?.goal && view.runId) map[view.runId] = view.plan.goal;
    return map;
  }, [view.plan?.goal, view.runId]);

  return (
    <div className={`ws${showPane ? " with-pane" : ""}`} data-testid="preview-workspace">
      <Sidebar view={view} activeRunId={view.runId} goals={goals} />
      <main className="ws-centre" id="ws-main" tabIndex={-1}>
        <CentreHeader view={view} />
        <CentreBody view={view} hideVerdict={showPane && pane === "review"} />
        {view.status === "ready" && connectionState === "reconnecting" ? (
          <p role="status" className="ws-conn-note">
            Reconnecting to the live event stream…
          </p>
        ) : null}
      </main>
      {showPane && view.agg ? (
        <aside className="ws-pane" aria-label={pane === "review" ? "Review" : "Diff"}>
          <div className="ws-pane-head">
            <h2 className="ws-pane-title">{pane === "review" ? "Review" : "Diff"}</h2>
            <Link href={`/preview/run?run=${encodeURIComponent(view.agg.runId)}`} className="ws-pane-close" aria-label="Close the pane">
              ✕
            </Link>
          </div>
          {pane === "diff" ? <DiffPane view={view} /> : <ReviewPane view={view} />}
        </aside>
      ) : null}
    </div>
  );
}
