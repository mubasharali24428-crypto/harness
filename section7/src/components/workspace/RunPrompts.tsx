"use client";

// R3: what the run asks of a person, shown in the run column where it happens.
// Pending approval requests carry their own Approve / Reject buttons (the same
// decision call as the Advanced pane; nothing is ever approved automatically),
// and a stopped run ends with one recap, one question and one button.

import * as React from "react";
import Link from "next/link";
import { loadErrorMessage } from "@/lib/humanize";
import { Button } from "@/components/ui";
import { roleCanAct } from "../../../app/dashboard/planGateApi";
import { decideApproval, listApprovals, type Approval } from "../../../app/projects/[projectId]/runs/[runId]/collabApi";
import { nextStep, type NextStep } from "./runFlow";

export function InlineApprovals({
  runId,
  token,
  role,
  tick,
}: {
  runId: string;
  token: string;
  role?: string | null;
  /** Changes whenever the run is re-read, so the list follows the run. */
  tick: unknown;
}) {
  const [pending, setPending] = React.useState<Approval[]>([]);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(
    () => listApprovals(runId, token)
      .then((r) => setPending(r.approvals.filter((a) => a.state === "pending")))
      .catch(() => {}), // the Advanced pane reports approval load failures; the column stays quiet
    [runId, token],
  );
  React.useEffect(() => {
    void load();
  }, [load, tick]);

  if (pending.length === 0) return null;
  const canAct = roleCanAct(role);

  async function decide(a: Approval, approve: boolean) {
    setBusy(a.id);
    setError(null);
    try {
      await decideApproval(runId, token, a, approve);
    } catch (e) {
      setError(loadErrorMessage(e));
    } finally {
      setBusy(null);
      void load();
    }
  }

  return (
    <section className="ws-approvals" aria-label="Approval requests">
      {pending.map((a) => (
        <article key={a.id} className="ws-approval" data-testid="inline-approval">
          <h2 className="ws-approval-title">
            The agent asks to {a.action === "delete" ? "delete" : "change"} {a.paths.length === 1 ? "a file" : `${a.paths.length} files`}
          </h2>
          <p className="ws-approval-summary">{a.summary}</p>
          <ul className="ws-approval-paths">
            {a.paths.map((p) => (
              <li key={p}><code>{p}</code></li>
            ))}
          </ul>
          {a.diff ? (
            <details>
              <summary>Show the proposed change</summary>
              <pre className="ws-approval-diff" tabIndex={0} aria-label="Proposed change">{a.diff}</pre>
            </details>
          ) : null}
          <div className="ws-planpin-actions">
            <span title={canAct ? undefined : "Only admins and engineers can decide."}>
              <Button variant="primary" size="sm" disabled={!canAct || busy !== null} onClick={() => void decide(a, true)}>
                Approve
              </Button>
            </span>
            <span title={canAct ? undefined : "Only admins and engineers can decide."}>
              <Button size="sm" disabled={!canAct || busy !== null} onClick={() => void decide(a, false)}>
                Reject
              </Button>
            </span>
          </div>
        </article>
      ))}
      {error && <p role="alert" className="ws-composer-error">{error}</p>}
    </section>
  );
}

export function NextStepBox({
  state,
  onAction,
  busy = false,
}: {
  state: string;
  onAction: (action: Exclude<NextStep["action"], "new">) => void;
  busy?: boolean;
}) {
  const step = nextStep(state);
  if (!step) return null;
  return (
    <section className="ws-next" aria-label="Next step" data-testid="next-step">
      <p className="ws-next-recap">{step.recap}</p>
      <p className="ws-next-question">{step.question}</p>
      {step.action === "new" ? (
        <Link href="/" className="ws-next-link">{step.label}</Link>
      ) : (
        <Button variant="primary" size="sm" disabled={busy} onClick={() => onAction(step.action as Exclude<NextStep["action"], "new">)}>
          {step.label}
        </Button>
      )}
    </section>
  );
}
