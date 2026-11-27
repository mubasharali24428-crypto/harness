"use client";

// Operability slice 2: the run header leads with status. Goal, state, spend (or the word
// "unknown"), and at most one next action that opens an existing pane (Open approval / Open diff).
// When the event connection drops, the header says Reconnecting and keeps the last state shown.
// Hook-free, so the node-env suite drives it with fixtures.
import * as React from "react";
import { formatMoneyUsd, formatRelativeTime } from "@/lib/format";
import { stateLabel } from "../../../app/runs/runsApi";
import { runRowTitle } from "./runRows";
import type { PaneId } from "./panes";

export interface HeaderAction {
  pane: Extract<PaneId, "plan" | "diff">;
  label: "Open approval" | "Open diff";
}

/** The one next action the header offers, or null when the run asks nothing of a person here.
 *  Plan approval lives in the Plan pane; review of the changes lives in the Diff pane. */
export function headerNextAction(state: string, planApproved = false): HeaderAction | null {
  if (state === "awaiting_plan_approval" && !planApproved) return { pane: "plan", label: "Open approval" };
  if (state === "pr_opened" || state === "reviewing" || state === "done" || state === "completed") {
    return { pane: "diff", label: "Open diff" };
  }
  return null;
}

export const RECONNECTING_TEXT = "Reconnecting: this is the last state the server reported.";

export interface RunStatusHeaderProps {
  goal: string | null | undefined;
  state: string;
  planApproved?: boolean;
  costUsd: number | null | undefined;
  estimatedCostUsd?: number | null;
  startedAt: string | null;
  /** True when the last refresh failed: the data on screen is the last good answer. */
  stale: boolean;
  onOpenPane: (pane: HeaderAction["pane"]) => void;
  /** Run controls and links that already sat in the header (Pause/Resume, presence, PR link). */
  controls?: React.ReactNode;
  note?: string | null;
  now?: number;
}

const unknown = <span className="ws-unknown">unknown</span>;

export function RunStatusHeader({
  goal, state, planApproved = false, costUsd, estimatedCostUsd = null, startedAt, stale, onOpenPane, controls, note, now,
}: RunStatusHeaderProps) {
  const spend = formatMoneyUsd(costUsd ?? null);
  const estimate = formatMoneyUsd(estimatedCostUsd ?? null);
  const action = headerNextAction(state, planApproved);
  return (
    <header className="ws-run-head" aria-labelledby="ws-run-title">
      <div className="ws-run-head-row">
        <span className={`ws-dot lg ${state}`} aria-hidden="true" />
        <h1 id="ws-run-title" className="ws-run-title">{runRowTitle(goal)}</h1>
        {action ? (
          <button type="button" className="btn primary" data-next-action={action.pane} onClick={() => onOpenPane(action.pane)}>
            {action.label}
          </button>
        ) : null}
        {controls}
      </div>
      <p className="ws-run-meta">
        <span data-field="state">
          <span className="sr-only">State: </span>
          {stateLabel(state)}
        </span>
        <span aria-hidden="true">·</span>
        <span data-field="spend" title="Metered spend for this run (cost ledger)">
          Spend {spend === "unknown" ? unknown : spend}
        </span>
        <span aria-hidden="true">·</span>
        <span title="The plan's own estimate, not actual spend">
          Estimated {estimate === "unknown" ? unknown : estimate}
        </span>
        <span aria-hidden="true">·</span>
        <span>Started {startedAt ? formatRelativeTime(startedAt, now) : unknown}</span>
      </p>
      {note ? <p role="status" className="ws-conn-note">{note}</p> : null}
      {stale ? <p role="status" className="ws-conn-note" data-conn="reconnecting">{RECONNECTING_TEXT}</p> : null}
    </header>
  );
}
