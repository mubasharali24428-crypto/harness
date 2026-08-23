"use client";

// W1-C2 (C-005 UI half) — the PlanGate card. Renders the plan-gate lifecycle
// from RunStatus.state (Section 4's REAL AgentState vocabulary): propose →
// approve/reject → start, with exactly one enabled action set per state and
// role gates (viewer/guest never act). All wire traffic goes through
// planGateApi.ts — this component invents no ids, no endpoints, no fallback.
import { useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too, so one source fits both.
import * as React from "react";
import type { AgentPlan } from "@contracts/plan";
import type { RunStatus } from "@contracts/api";
import type { Principal } from "@contracts/roles";
import { ApiError, getRunStatus } from "@/lib/api";
import {
  panelStyle,
  headingStyle,
  buttonStyle,
  dangerButtonStyle,
  inputStyle,
  pillStyle,
} from "@/lib/styles";
import {
  approvePlan,
  classifyGatePhase,
  gateActionsFor,
  gateCaption,
  proposePlan,
  rejectPlan,
  startRun,
  type GateActions,
  type GateRole,
  type PlanGatePhase,
} from "./planGateApi";

const PHASE_PILL_COLOR: Record<PlanGatePhase, string> = {
  "no-plan": "#9aa4b2",
  "awaiting-approval": "#f59e0b",
  "ready-to-start": "#22c55e",
  "in-flight": "#3b82f6",
  done: "#9aa4b2",
};

/** Wire-call bundle the card needs. Each method delegates to planGateApi.ts;
 *  PlanGateCard builds this from the session token + server-minted run id. */
export interface GateWireActions {
  /** Server-minted run binding (from approve), null until one exists. */
  runId: string | null;
  propose: (goal: string) => void;
  approve: () => void;
  reject: () => void;
  start: () => void;
}

/**
 * Connected card: pass a live `status` snapshot (or let it poll via token),
 * the principal for role gating, and it renders the gate. Drop-in for the
 * dashboard / WorkingArea; exported as a named component so tests can drive
 * it without Next routing.
 */
export function PlanGateCard({
  token,
  principal,
  status: initialStatus,
  runId: initialRunId = null,
  onAfterAction,
}: {
  token: string;
  principal: Principal | null;
  /** Latest known RunStatus snapshot; null = not loaded yet. */
  status?: RunStatus | null;
  /** Server-minted run id from a previous approve in this session. */
  runId?: string | null;
  onAfterAction?: () => void;
}) {
  const [goalDraft, setGoalDraft] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(initialRunId);
  const [status, setStatus] = useState<RunStatus | null>(initialStatus ?? null);

  async function refresh() {
    if (!activeRunId) return;
    try {
      setStatus(await getRunStatus(activeRunId, token));
    } catch {
      /* snapshot stays as-is; next event/poll refreshes it */
    }
  }

  async function act(label: string, successNote: string, fn: () => Promise<void>) {
    setBusyAction(label);
    setError(null);
    setNote(null);
    try {
      await fn();
      await refresh();
      setNote(successNote);
      onAfterAction?.();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message} (${err.code})` : String(err));
    } finally {
      setBusyAction(null);
    }
  }

  function activePlanId(): string {
    if (!status?.planId) throw new Error("No active plan to act on.");
    return status.planId;
  }

  const wire: GateWireActions = {
    runId: activeRunId,
    propose: (goal) =>
      void act("propose", "Plan proposed — awaiting approval.", async () => {
        await proposePlan(goal.trim(), token);
      }),
    approve: () =>
      void act("approve", "Plan approved — start when ready.", async () => {
        // The orchestrator mints and returns the run binding; we store what the
        // server returned and nothing else.
        const res = await approvePlan(status?.planId ?? "", token);
        setActiveRunId(res.runId);
      }),
    reject: () =>
      void act("reject", "Plan rejected and audited.", async () => {
        await rejectPlan(status?.planId ?? "", token);
      }),
    start: () =>
      void act("start", "Run started.", async () => {
        if (!activeRunId) throw new Error("No run binding yet — approve the plan first.");
        await startRun(activeRunId, token);
      }),
  };

  const phase = classifyGatePhase(status?.state, status?.planApproved, Boolean(status?.planId));
  return (
    <PlanGateView
      phase={phase}
      enabled={gateActionsFor(phase, principal?.role ?? null, busyAction !== null)}
      // The card never holds the full AgentPlan object (parents fetch that via
      // GET /runs/{id}/plan); it only echoes the operator's typed goal.
      plan={goalDraft.trim() !== "" ? { goal: goalDraft } : null}
      goalDraft={goalDraft}
      onGoalDraftChange={setGoalDraft}
      busyAction={busyAction}
      error={error}
      note={note}
      actions={wire}
    />
  );
}

/** Pure presentational core — all gating logic lives in planGateApi.ts.
 *  Exported for component tests of state-driven rendering. */
export function PlanGateView({
  phase,
  enabled,
  plan,
  goalDraft,
  onGoalDraftChange,
  busyAction,
  error,
  note,
  actions,
}: {
  phase: PlanGatePhase;
  enabled: GateActions;
  plan: Pick<AgentPlan, "goal"> | null;
  goalDraft: string;
  onGoalDraftChange: (goal: string) => void;
  busyAction: string | null;
  error: string | null;
  note: string | null;
  actions: GateWireActions;
}) {
  const showProposeForm = phase === "no-plan" && !plan;
  return (
    <section style={panelStyle} data-testid="plan-gate">
      <h2 style={headingStyle}>Plan gate</h2>
      <p>
        <span style={pillStyle(PHASE_PILL_COLOR[phase])} data-testid="plan-gate-phase">
          {phase}
        </span>{" "}
        <span style={{ color: "#9aa4b2", fontSize: 13 }}>{gateCaption(phase)}</span>
      </p>

      {showProposeForm ? (
        <div>
          <input
            value={goalDraft}
            onChange={(e) => onGoalDraftChange(e.target.value)}
            placeholder="Describe the goal for this run…"
            style={inputStyle}
            aria-label="Run goal"
            disabled={busyAction !== null}
          />
          <button
            style={{ ...buttonStyle, marginTop: 8 }}
            disabled={!enabled.propose || !goalDraft.trim()}
            onClick={() => actions.propose(goalDraft)}
            data-testid="propose-plan"
          >
            {busyAction === "propose" ? "Proposing…" : "Propose plan"}
          </button>
        </div>
      ) : (
        <p style={{ color: "#6b7280", fontSize: 13, margin: "8px 0 0" }}>
          Goal: {(plan?.goal ?? "").trim() !== "" ? plan!.goal : "(no goal recorded)"}
        </p>
      )}

      {phase === "awaiting-approval" && (
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button
            style={buttonStyle}
            disabled={!enabled.approve}
            onClick={() => actions.approve()}
            data-testid="approve-plan"
          >
            {busyAction === "approve" ? "Approving…" : "Approve plan"}
          </button>
          <button
            style={dangerButtonStyle}
            disabled={!enabled.reject}
            onClick={() => actions.reject()}
            data-testid="reject-plan"
          >
            {busyAction === "reject" ? "Rejecting…" : "Reject"}
          </button>
        </div>
      )}

      {phase === "ready-to-start" && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
          <button
            style={buttonStyle}
            disabled={!enabled.start}
            onClick={() => actions.start()}
            data-testid="start-run"
          >
            {busyAction === "start" ? "Starting…" : "Start run"}
          </button>
          {actions.runId && (
            <span style={{ color: "#6b7280", fontSize: 12 }} title="Server-minted run binding">
              run {actions.runId}
            </span>
          )}
        </div>
      )}

      {(phase === "in-flight" || phase === "done") && (
        <p style={{ color: "#9aa4b2", fontSize: 13, margin: "8px 0 0" }}>
          No plan-gate action available in this state.
        </p>
      )}

      {note && (
        <p style={{ color: "#22c55e", fontSize: 13, margin: "10px 0 0" }} role="status">
          {note}
        </p>
      )}
      {error && (
        <p style={{ color: "#f87171", fontSize: 13, margin: "10px 0 0" }} role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
