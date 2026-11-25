"use client";

// Operability slice 3: an approval card a non-expert can finish. It leads with what happens in
// plain words and one line of why; the file list and the diff stay one control away (a closed
// <details>). Similar past cases are read-only text: no button here turns a lesson into a rule
// (CD-02). Approve and Deny carry distinct accessible names that name the change. A decision that
// succeeded leaves a receipt on the card; one that failed keeps its error on the card.
// Nothing is approved automatically: both buttons call the existing decision endpoint.
import * as React from "react";
import { Button } from "@/components/ui";
import { formatDateTime } from "@/lib/intl";
import { loadErrorMessage } from "@/lib/humanize";
import { decideApproval, type Approval } from "../../../app/projects/[projectId]/runs/[runId]/collabApi";

/** What approving does, in plain words. */
export function plainOutcome(a: Pick<Approval, "action" | "paths">): string {
  const what = a.paths.length === 1 ? a.paths[0] : `${a.paths.length} files`;
  return a.action === "delete"
    ? `If you approve, the agent deletes ${what}.`
    : `If you approve, the agent changes ${what}.`;
}

/** One line of why: the server's plain presentation when it sent one, else the summary's first line. */
export function plainReason(a: Pick<Approval, "plain" | "summary">): string {
  const src = (a.plain ?? "").trim() || (a.summary ?? "").trim();
  const line = src.split(/\r?\n/, 1)[0]?.trim() ?? "";
  return line || "The agent gave no reason.";
}

/** Distinct accessible names for the two decisions on one card. */
export function decisionNames(a: Pick<Approval, "action" | "paths">): { approve: string; deny: string } {
  const verb = a.action === "delete" ? "deleting" : "changing";
  const what = a.paths.length === 1 ? a.paths[0] : `${a.paths.length} files`;
  return { approve: `Approve ${verb} ${what}`, deny: `Deny ${verb} ${what}` };
}

export type DecisionResult =
  | { ok: true; approval: Approval; approve: boolean }
  | { ok: false; error: string; approve: boolean };

/** Receipt text from the server's answer: its state and who decided, never a guess. */
export function receiptText(r: Extract<DecisionResult, { ok: true }>): string {
  const a = r.approval;
  const word = a.state === "approved" ? "Approved" : a.state === "rejected" ? "Denied" : `Recorded (${a.state})`;
  const who = a.decidedBy ? ` by ${a.decidedBy}` : "";
  const when = a.decidedAt ? ` on ${formatDateTime(a.decidedAt)}` : "";
  const after =
    a.state === "approved" && a.requester === "system:staging"
      ? " The change is not applied yet: applying it is a separate step."
      : "";
  return `${word}${who}${when}.${after}`;
}

/** One decision call; the result is what the card shows next. */
export async function decide(runId: string, token: string, a: Approval, approve: boolean): Promise<DecisionResult> {
  try {
    return { ok: true, approval: await decideApproval(runId, token, a, approve), approve };
  } catch (e) {
    return { ok: false, error: loadErrorMessage(e), approve };
  }
}

export interface ApprovalCardProps {
  approval: Approval;
  canAct: boolean;
  busy: boolean;
  result?: DecisionResult | null;
  onDecide: (a: Approval, approve: boolean) => void;
}

export function ApprovalCard({ approval: a, canAct, busy, result = null, onDecide }: ApprovalCardProps) {
  const names = decisionNames(a);
  const titleId = `approval-${a.id}-title`;
  const decided = result?.ok === true;
  const noRight = canAct ? undefined : "Only admins and engineers can decide.";
  return (
    <article className="ws-approval" data-testid="inline-approval" aria-labelledby={titleId}>
      <h2 id={titleId} className="ws-approval-title">{plainOutcome(a)}</h2>
      <p className="ws-approval-summary" data-field="reason">{plainReason(a)}</p>

      {a.similarCases && a.similarCases.length > 0 ? (
        <div className="ws-approval-similar" data-field="similar-cases">
          <p className="ws-approval-note">Seen before in earlier runs (for your information only):</p>
          <ul className="ws-approval-paths">
            {a.similarCases.slice(0, 3).map((c) => (
              <li key={c.caseId}>
                {c.outcome}
                {c.suggestedRule ? `: ${c.suggestedRule}` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <details className="ws-approval-tech">
        <summary>Technical details</summary>
        <ul className="ws-approval-paths" aria-label="Files">
          {a.paths.map((p) => (
            <li key={p}><code>{p}</code></li>
          ))}
        </ul>
        {a.summary && a.summary.trim() !== plainReason(a) ? <p className="ws-approval-summary">{a.summary}</p> : null}
        {a.diff ? (
          <pre className="ws-approval-diff" tabIndex={0} aria-label="Proposed change">{a.diff}</pre>
        ) : (
          <p className="ws-approval-note">No diff was attached to this request.</p>
        )}
      </details>

      {decided ? (
        <p role="status" className="ws-approval-receipt" data-field="receipt">
          {receiptText(result)}
        </p>
      ) : (
        <div className="ws-planpin-actions" role="group" aria-label="Decide">
          <span title={noRight}>
            <Button variant="primary" size="sm" aria-label={names.approve} disabled={!canAct || busy}
              onClick={() => onDecide(a, true)}>
              Approve
            </Button>
          </span>
          <span title={noRight}>
            <Button size="sm" aria-label={names.deny} disabled={!canAct || busy} onClick={() => onDecide(a, false)}>
              Deny
            </Button>
          </span>
        </div>
      )}
      {result && !result.ok ? (
        <p role="alert" className="ws-composer-error" data-field="error">
          {result.approve ? "Approve" : "Deny"} did not go through: {result.error}
        </p>
      ) : null}
    </article>
  );
}
