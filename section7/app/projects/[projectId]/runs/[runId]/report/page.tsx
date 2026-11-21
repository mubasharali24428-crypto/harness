"use client";

// Read-only report of one run: what was asked, what changed, who approved,
// what the tests and risk checks recorded, what it cost. No controls.
//
// Scope: any signed-in member of the run's team can open it (a viewer too);
// another team gets "not found" from the server. It is NOT the P-17 client
// portal: an end-client outside the team has no role to sign in with yet.

import { use, useEffect, useState } from "react";
import { loadSession } from "@/lib/auth";
import { ErrorState, LoadingState } from "@/components/States";
import { useRun } from "@/components/workspace/useRun";
import { DiffPane } from "@/components/workspace/DiffPane";
import { RunEvidence } from "@/components/workspace/RunEvidence";
import { runRowTitle } from "@/components/workspace/previewData";
import { reviewVerdictFacts } from "../../../../../review/verdictCard";
import { stateLabel } from "../../../../../runs/runsApi";

export default function RunReportPage({ params }: { params: Promise<{ projectId: string; runId: string }> }) {
  const { projectId, runId } = use(params);
  const [token, setToken] = useState<string | null | undefined>(undefined);
  // P-17: a guest (a partner's end-client) reads this page from the portal.
  const [guest, setGuest] = useState(false);
  useEffect(() => {
    const session = loadSession();
    setToken(session?.token ?? null);
    setGuest(session?.principal.role === "guest");
  }, []);
  const view = useRun(runId, token ?? null);
  const { agg } = view;

  return (
    <main className="ws-report" data-testid="run-report">
      <p className="ws-report-kicker">Read-only report · nothing on this page changes the run</p>
      {token === null ? (
        <ErrorState title="Sign in to read this report" message="Reports are visible to members of the run's team." />
      ) : view.status === "notFound" ? (
        <ErrorState title="Run not found" message="No run with this id is visible to your team." />
      ) : view.status === "error" ? (
        <ErrorState title="The report didn’t load" message={view.error ?? "Something went wrong."} onRetry={view.retry} />
      ) : !agg || !token ? (
        <LoadingState label="Loading the report…" rows={4} />
      ) : (
        <>
          <h1>{runRowTitle(view.plan?.goal)}</h1>
          <p className="ws-report-meta">
            {stateLabel(agg.state)} · run <code>{agg.runId}</code>
            {agg.url ? <> · <a href={agg.url} target="_blank" rel="noreferrer">Pull request ↗</a></> : null}
          </p>

          <h2>What was planned</h2>
          {view.plan ? (
            <ol>{(view.plan.steps ?? []).map((s, i) => <li key={i}>{s}</li>)}</ol>
          ) : (
            <p className="ws-evidence-note">No plan is recorded for this run.</p>
          )}

          <h2>Recorded facts</h2>
          <dl className="ws-verdict-grid">
            {reviewVerdictFacts(view.reviewState === "ready" ? view.review : null, agg).map((fact) => (
              <div key={fact.key} className={`ws-verdict-fact${fact.unknown ? " unknown" : ""}`}>
                <dt>{fact.label}</dt>
                <dd>
                  {fact.value}
                  {fact.details.length > 0 ? <ul>{fact.details.map((d, i) => <li key={i}>{d}</li>)}</ul> : null}
                </dd>
              </div>
            ))}
          </dl>

          <h2>Cost and trace</h2>
          <RunEvidence agg={agg} token={token} canExport={!guest} />

          <h2>What changed</h2>
          <DiffPane view={view} />

          <p className="ws-evidence-links">
            {guest
              ? <a className="ws-foot-link" href="/portal">← All runs</a>
              : <a className="ws-foot-link" href={`/projects/${projectId}/runs/${runId}`}>Open the run →</a>}
          </p>
        </>
      )}
    </main>
  );
}
