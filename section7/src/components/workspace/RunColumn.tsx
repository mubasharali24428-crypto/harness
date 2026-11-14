"use client";

// RunColumn — the centre pane's one scrolling column of step blocks. Each block
// collapses to one line (verb, target, duration, cost) and opens to its receipt.
// Steps come from the run aggregate's server-scrubbed details via the existing
// timeline rules (groupEvents); receipts stay short and human. The verdict card
// (existing component, real facts) closes a finished run.

import * as React from "react";
import { VerdictCard, reviewVerdictFacts } from "../../../app/review/verdictCard";
import { stepsToEvents, groupEvents } from "@/lib/runTimeline";
import { formatTimestamp } from "@/lib/format";
import type { PreviewView } from "./usePreviewRun";

/** Receipts that merely restate the verb ("terminal output recorded" under
 *  "Terminal output") add no information; the collapsed line shows target
 *  facts only. */
function meaningfulReceipt(label: string, receipt: string | null): string | null {
  if (!receipt) return null;
  const restates =
    /^terminal output recorded$/i.test(receipt) ||
    /^diff updated$/i.test(receipt) ||
    label.toLowerCase().startsWith(receipt.toLowerCase());
  return restates ? null : receipt;
}

/** Verb-first line for a block: human label, then receipt facts. */
function blockLine(label: string, receipt: string | null, durationMs: number | null): React.ReactNode {
  return (
    <>
      <span className="ws-step-verb">{label}</span>
      {receipt ? <span className="ws-step-target">{receipt}</span> : null}
      <span className="ws-step-numbers">
        {durationMs !== null ? <span>{durationMs >= 1000 ? `${(durationMs / 1000).toFixed(1)}s` : `${durationMs}ms`}</span> : null}
      </span>
    </>
  );
}

/** Duration + per-step cost receipt from the scrubbed detail; null stays null. */
function durationOf(detail: Record<string, string | number | boolean> | null | undefined): number | null {
  const v = detail?.durationMs;
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;
}

function StepBlock({
  label,
  receipt,
  ts,
  status,
  durationMs,
  costNote,
  open,
  children,
}: {
  label: string;
  receipt: string | null;
  ts: string | null;
  status: string;
  durationMs: number | null;
  costNote: string | null;
  open?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <details className={`ws-step ${status}`} open={open}>
      <summary className="ws-step-line">
        {blockLine(label, receipt, durationMs)}
        <span className="ws-step-ts">{ts ? formatTimestamp(ts).split(",").pop()?.trim() : ""}</span>
      </summary>
      <div className="ws-step-receipt">
        {ts ? (
          <p>
            <span className="ws-receipt-key">When</span> {formatTimestamp(ts)}
          </p>
        ) : (
          <p>
            <span className="ws-receipt-key">When</span> <span className="ws-unknown">not recorded</span>
          </p>
        )}
        {durationMs !== null ? (
          <p>
            <span className="ws-receipt-key">Duration</span> {durationMs} ms
          </p>
        ) : null}
        <p>
          <span className="ws-receipt-key">Cost</span>{" "}
          {costNote ?? <span className="ws-unknown">not recorded per step</span>}
        </p>
        {children}
      </div>
    </details>
  );
}

export function RunColumn({ view }: { view: PreviewView }) {
  const { agg, plan, review, runId } = view;
  if (!agg || !runId) return null;
  const blocks = React.useMemo(() => groupEvents(stepsToEvents(agg.steps)), [agg.steps]);
  const finished = /^(pr_opened|done|completed|stopped|cancelled|failed)$/i.test(agg.state);
  const verdict = review || agg ? reviewVerdictFacts(review ?? null, agg) : [];

  return (
    <div className="ws-column">
      {blocks.length === 0 ? (
        <p className="ws-empty-note">No steps recorded for this run yet.</p>
      ) : (
        <ol className="ws-steps">
          {blocks.map((b, i) => {
            const first = b.seq;
            const detail = agg.steps.find((s) => s.seq === first)?.detail ?? null;
            return (
              <li key={b.id}>
                <StepBlock
                  label={b.label}
                  receipt={meaningfulReceipt(b.label, b.receipt)}
                  ts={b.ts}
                  status={b.status}
                  durationMs={durationOf(detail)}
                  costNote={null}
                  open={false}
                />
              </li>
            );
          })}
        </ol>
      )}

      {plan ? (
        <section className="ws-plan" aria-label="Plan">
          <h2 className="ws-plan-title">Plan</h2>
          <ol className="ws-plan-steps">
            {plan.steps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
        </section>
      ) : null}

      {finished ? <VerdictCard facts={verdict} /> : null}
    </div>
  );
}
