"use client";

// R3: what the run asks of a person, shown in the run column where it happens.
// Pending approval requests carry their own Approve / Deny buttons (the same
// decision call as the Advanced pane; nothing is ever approved automatically),
// and a stopped run ends with one recap, one question and one button.

import * as React from "react";
import Link from "next/link";
import { Button } from "@/components/ui";
import { roleCanAct } from "../../../app/dashboard/planGateApi";
import { listApprovals, type Approval } from "../../../app/projects/[projectId]/runs/[runId]/collabApi";
import { nextStep, type NextStep } from "./runFlow";
import { ApprovalCard, decide, type DecisionResult } from "./ApprovalCard";

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
  // Operability slice 3: the last decision per card. A success keeps its card on screen with a
  // receipt even after the pending list drops it; a failure keeps its error on its own card.
  const [results, setResults] = React.useState<Record<string, { approval: Approval; result: DecisionResult }>>({});

  const load = React.useCallback(
    () => listApprovals(runId, token)
      .then((r) => setPending(r.approvals.filter((a) => a.state === "pending")))
      .catch(() => {}), // the Advanced pane reports approval load failures; the column stays quiet
    [runId, token],
  );
  React.useEffect(() => {
    void load();
  }, [load, tick]);

  const cards = cardsToShow(pending, results);
  if (cards.length === 0) return null;
  const canAct = roleCanAct(role);

  async function onDecide(a: Approval, approve: boolean) {
    setBusy(a.id);
    const result = await decide(runId, token, a, approve);
    setResults((prev) => ({ ...prev, [a.id]: { approval: a, result } }));
    setBusy(null);
    void load();
  }

  return (
    <section className="ws-approvals" aria-label="Approval requests">
      {cards.map(({ approval, result }) => (
        <ApprovalCard key={approval.id} approval={approval} canAct={canAct} busy={busy !== null}
          result={result} onDecide={(x, ok) => void onDecide(x, ok)} />
      ))}
    </section>
  );
}

/** Pending cards in server order, then cards decided here that the server no longer lists as
 *  pending (their receipt stays visible). A card that failed keeps its error until the next try. */
export function cardsToShow(
  pending: Approval[],
  results: Record<string, { approval: Approval; result: DecisionResult }>,
): Array<{ approval: Approval; result: DecisionResult | null }> {
  const ids = new Set(pending.map((a) => a.id));
  const out: Array<{ approval: Approval; result: DecisionResult | null }> = pending.map((a) => ({
    approval: a,
    result: results[a.id]?.result ?? null,
  }));
  for (const [id, r] of Object.entries(results)) {
    if (!ids.has(id) && r.result.ok) out.push({ approval: r.approval, result: r.result });
  }
  return out;
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
