"use client";

// R3 pinned plan: sits above the composer. Collapsed it is one line (step count
// and where the plan stands) with the action a person owes: Approve, then Start.
// Opened it lists the steps; Edit turns them into plain text areas (one item per
// line, no JSON) and saving records a new plan version. All calls are the
// existing plan-version routes (docs/plans.md) and the run start route.

import * as React from "react";
import type { AgentPlan } from "@contracts/plan";
import { loadErrorMessage } from "@/lib/humanize";
import { Button } from "@/components/ui";
import { getRunStatus } from "@/lib/api";
import { rejectPlan, roleCanAct, startRun } from "../../../app/dashboard/planGateApi";
import { call, post, root } from "../../../app/projects/[projectId]/runs/[runId]/collabApi";
import { linesToList } from "./runFlow";

interface Versions {
  latestVersion: number | null;
  locked: boolean;
}

const versionsUrl = (runId: string) => `${root(runId)}/plan/versions`;

export function PlanPin({
  runId,
  token,
  role,
  plan,
  state,
  onChanged,
}: {
  runId: string;
  token: string;
  role?: string | null;
  plan: AgentPlan | null;
  state: string;
  /** Called after a save, an approval or a start so the page re-reads the run. */
  onChanged: () => void;
}) {
  const [versions, setVersions] = React.useState<Versions | null>(null);
  const [editing, setEditing] = React.useState(false);
  const [goal, setGoal] = React.useState("");
  const [steps, setSteps] = React.useState("");
  const [tests, setTests] = React.useState("");
  const [busy, setBusy] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const awaiting = state === "awaiting_plan_approval";
  const loadVersions = React.useCallback(
    () => call<Versions>(versionsUrl(runId), token).then(setVersions).catch((e) => setError(loadErrorMessage(e))),
    [runId, token],
  );
  React.useEffect(() => {
    if (awaiting) void loadVersions();
  }, [awaiting, loadVersions]);

  if (!plan) return null;
  const canAct = roleCanAct(role);
  const why = canAct ? undefined : "Only admins and engineers can decide on a plan.";
  const approved = !awaiting || versions?.locked === true;
  const stepCount = (plan.steps ?? []).length;
  const standing = !awaiting ? "approved" : approved ? "approved, not started" : "waiting for approval";

  async function act(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
      await loadVersions();
      onChanged();
    } catch (e) {
      setError(loadErrorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  function startEditing() {
    setGoal(plan!.goal);
    setSteps((plan!.steps ?? []).join("\n"));
    setTests((plan!.tests ?? []).join("\n"));
    setEditing(true);
  }

  const save = () =>
    act("save", async () => {
      await post(versionsUrl(runId), token, {
        // Fields the editor does not show are carried over unchanged.
        content: {
          goal: goal.trim(),
          steps: linesToList(steps),
          tests: linesToList(tests),
          risks: plan!.risks ?? [],
          filesLikelyToChange: plan!.filesLikelyToChange ?? [],
          forbiddenPaths: plan!.forbiddenPaths ?? [],
          expectedResult: plan!.expectedResult ?? "",
          estimatedCostUsd: plan!.estimatedCostUsd ?? 0,
        },
        expectedVersion: versions?.latestVersion ?? 0,
        requestId: crypto.randomUUID(),
      });
      setEditing(false);
    });

  return (
    <details className="ws-planpin" data-testid="plan-pin" open={awaiting || undefined}>
      <summary>
        <span className="ws-planpin-title">Plan</span>
        <span className="ws-planpin-meta">
          {stepCount} {stepCount === 1 ? "step" : "steps"} · {standing}
        </span>
      </summary>

      {editing ? (
        <div className="ws-planpin-edit">
          <label>
            Goal
            <textarea rows={2} value={goal} onChange={(e) => setGoal(e.target.value)} />
          </label>
          <label>
            Steps, one per line
            <textarea rows={Math.max(3, linesToList(steps).length + 1)} value={steps} onChange={(e) => setSteps(e.target.value)} />
          </label>
          <label>
            Tests, one per line
            <textarea rows={2} value={tests} onChange={(e) => setTests(e.target.value)} />
          </label>
          <div className="ws-planpin-actions">
            <Button variant="primary" size="sm" disabled={busy !== null || !goal.trim()} onClick={() => void save()} testId="plan-save">
              {busy === "save" ? "Saving…" : "Save as a new version"}
            </Button>
            <Button size="sm" disabled={busy !== null} onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <>
          <ol className="ws-plan-steps">
            {(plan.steps ?? []).map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
          {awaiting && (
            <div className="ws-planpin-actions">
              {!approved && (
                <>
                  <span title={why}>
                    <Button variant="primary" size="sm" testId="plan-approve"
                      disabled={!canAct || busy !== null || versions?.latestVersion == null}
                      onClick={() => void act("approve", () => post(`${versionsUrl(runId)}/${versions!.latestVersion}/approve`, token))}>
                      {busy === "approve" ? "Approving…" : "Approve plan"}
                    </Button>
                  </span>
                  <span title={why}>
                    <Button size="sm" disabled={!canAct || busy !== null} onClick={startEditing} testId="plan-edit">
                      Edit
                    </Button>
                  </span>
                  <span title={why}>
                    <Button size="sm" disabled={!canAct || busy !== null} testId="plan-reject"
                      onClick={() => void act("reject", async () => {
                        const planId = (await getRunStatus(runId, token)).planId;
                        if (!planId) throw new Error("This run has no plan to reject.");
                        await rejectPlan(planId, token);
                      })}>
                      {busy === "reject" ? "Rejecting…" : "Reject"}
                    </Button>
                  </span>
                </>
              )}
              {approved && (
                <span title={why}>
                  <Button variant="primary" size="sm" disabled={!canAct || busy !== null} testId="plan-start"
                    onClick={() => void act("start", () => startRun(runId, token))}>
                    {busy === "start" ? "Starting…" : "Start run"}
                  </Button>
                </span>
              )}
              {why && <span className="ws-composer-help">{why}</span>}
            </div>
          )}
        </>
      )}
      {error && <p role="alert" className="ws-composer-error">{error}</p>}
    </details>
  );
}
