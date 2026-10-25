"use client";

// W1-C2 (C-005 UI half) — the PlanGate card. Renders the plan-gate lifecycle
// from RunStatus.state (Section 4's REAL AgentState vocabulary): propose →
// approve/reject → start, with exactly one enabled action set per state and
// role gates (viewer/guest never act). All wire traffic goes through
// planGateApi.ts — this component invents no ids, no endpoints, no fallback.
import { useEffect, useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too, so one source fits both.
import * as React from "react";
import type { AgentPlan } from "@contracts/plan";
import type { RunStatus } from "@contracts/api";
import type { Principal } from "@contracts/roles";
import { ApiError, getRunStatus } from "@/lib/api";
import { getProjectId, setRunId } from "@/lib/auth";
import {
  panelStyle,
  headingStyle,
  buttonStyle,
  dangerButtonStyle,
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
  "no-plan": "#a8a296",
  "awaiting-approval": "#e5a814",
  "ready-to-start": "#8ee5a1",
  "in-flight": "#6cb8ff",
  done: "#a8a296",
};

const composerStyle: React.CSSProperties = {
  border: "1px solid var(--border-soft)",
  borderRadius: 12,
  background: "var(--input)",
  padding: 12,
};

const composerInputStyle: React.CSSProperties = {
  width: "100%",
  border: "none",
  background: "transparent",
  color: "inherit",
  font: "inherit",
  fontSize: 14,
  lineHeight: 1.5,
  minHeight: 64,
  padding: 0,
  resize: "vertical",
  outline: "none",
};

// M14-F4 — optimistic UI. While an approve/reject wire call is in flight the
// card RENDERS THE EXPECTED PHASE immediately instead of waiting for the
// round-trip, then reconciles with the server snapshot; a failure rolls the
// phase back to what the last server truth says and surfaces the error.
/** Pure: the phase the card shows = optimistic guess until it is cleared. */
export function effectivePhase(
  serverPhase: PlanGatePhase,
  optimistic: PlanGatePhase | null,
): PlanGatePhase {
  return optimistic ?? serverPhase;
}

/** Reject keeps its affordances pending until the 202 lands (W4-VT3 probe:
 *  forced-error must visibly snap back, never silently vanish the controls). */
export const OPTIMISTIC_PENDING_CAPTION = "Waiting for the server to confirm…";

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
  const [proposed, setProposed] = useState<AgentPlan | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(initialRunId);
  const [status, setStatus] = useState<RunStatus | null>(initialStatus ?? null);
  // M14-F4 — optimistic overlay: the phase the card pretends to be in while a
  // decision is on the wire. Null = render the last server truth.
  const [optimisticPhase, setOptimisticPhase] = useState<PlanGatePhase | null>(null);

  useEffect(() => {
    if (!activeRunId) return;
    let cancelled = false;
    getRunStatus(activeRunId, token).then((next) => { if (!cancelled) setStatus(next); })
      .catch((error) => { if (!cancelled) setError(String(error)); });
    return () => { cancelled = true; };
  }, [activeRunId, token]);

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
      setOptimisticPhase(null);
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
        const response = await proposePlan(goal.trim(), token, getProjectId());
        setProposed(response.plan);
        setStatus({ runId: "", planId: response.planId, state: "awaiting_plan_approval",
          planApproved: false, isHumanEditing: false });
      }),
    // M14-F4 — approve flips the card to ready-to-start INSTANTLY; the server
    // response reconciles it (real run binding) and a failure rolls back to
    // the last snapshot with the mapped error.
    approve: () => {
      const planId = status?.planId;
      void act("approve", "Plan approved — start when ready.", async () => {
        if (!planId) throw new Error("No active plan to act on.");
        setOptimisticPhase("ready-to-start");
        try {
          const res = await approvePlan(planId, token);
          setActiveRunId(res.runId);
          setRunId(res.runId);
          setStatus(await getRunStatus(res.runId, token));
          setOptimisticPhase(null);
        } catch (err) {
          setOptimisticPhase(null); // rollback to last server truth
          throw err;
        }
      });
    },
    // M14-F4 — reject keeps its affordances visible but pending until the
    // server 202 confirms (the run returns to IDLE with the plan still bound,
    // so the card itself stays — only the decision affordance resolves).
    reject: () => {
      const planId = status?.planId;
      void act("reject", "Plan rejected and audited.", async () => {
        if (!planId) throw new Error("No active plan to act on.");
        setOptimisticPhase("awaiting-approval"); // keep card, decision pending
        try {
          await rejectPlan(planId, token);
        } catch (err) {
          setOptimisticPhase(null); // rollback to last server truth
          throw err;
        }
      });
    },
    start: () =>
      void act("start", "Run started.", async () => {
        if (!activeRunId) throw new Error("No run binding yet — approve the plan first.");
        await startRun(activeRunId, token);
      }),
  };

  const serverPhase = classifyGatePhase(
    status?.state,
    status?.planApproved,
    Boolean(status?.planId),
  );
  // M14-F4 — the card renders the optimistic guess over server truth; act()'s
  // refresh() reconciles it against the real snapshot when the wire settles.
  const phase = effectivePhase(serverPhase, optimisticPhase);
  return (
    <>
    <PlanGateView
      phase={phase}
      enabled={gateActionsFor(phase, principal?.role ?? null, busyAction !== null)}
      optimisticPending={optimisticPhase !== null}
      // The card never holds the full AgentPlan object (parents fetch that via
      // GET /runs/{id}/plan); it only echoes the operator's typed goal.
      plan={proposed}
      goalDraft={goalDraft}
      onGoalDraftChange={setGoalDraft}
      busyAction={busyAction}
      error={error}
      note={note}
      actions={wire}
    />
    {activeRunId && getProjectId() && <a href={`/projects/${encodeURIComponent(getProjectId()!)}/runs/${encodeURIComponent(activeRunId)}`} style={{ color: "var(--accent)" }}>
      Open shared run
    </a>}
    </>
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
  // M14-F4 — true while an approve/reject decision is on the wire: the phase
  // pill already shows the optimistic guess and the caption says "pending".
  optimisticPending = false,
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
  optimisticPending?: boolean;
}) {
  const showProposeForm = phase === "no-plan" && !plan;
  const propose = () => {
    if (enabled.propose && goalDraft.trim() && busyAction === null) actions.propose(goalDraft);
  };
  return (
    <section style={panelStyle} data-testid="plan-gate">
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
          flexWrap: "wrap",
        }}
      >
        <h2 style={{ ...headingStyle, margin: 0 }}>Plan gate</h2>
        <span style={pillStyle(PHASE_PILL_COLOR[phase])} data-testid="plan-gate-phase">
          {phase}
        </span>
      </div>
      <p style={{ color: "var(--muted)", fontSize: 13, margin: "8px 0 12px" }}>
        {optimisticPending ? OPTIMISTIC_PENDING_CAPTION : gateCaption(phase)}
      </p>

      {showProposeForm ? (
        <div style={composerStyle}>
          <textarea
            value={goalDraft}
            onChange={(e) => onGoalDraftChange(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault();
                propose();
              }
            }}
            placeholder="Describe the goal for this run…"
            style={composerInputStyle}
            aria-label="Run goal"
            rows={3}
            disabled={busyAction !== null}
          />
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 8,
              marginTop: 8,
            }}
          >
            <span style={{ color: "var(--muted)", fontSize: 11 }}>⌘↵ to propose</span>
            <button
              style={buttonStyle}
              disabled={!enabled.propose || !goalDraft.trim()}
              onClick={propose}
              data-testid="propose-plan"
            >
              {busyAction === "propose" ? "Proposing…" : "Propose plan"}
            </button>
          </div>
        </div>
      ) : (
        // The goal is shown by the shared-run panel (read from the server); never print a false "none recorded" here.
        (plan?.goal ?? "").trim() !== "" ? <p style={{ color: "var(--muted)", fontSize: 13, margin: "8px 0 0" }}>Goal: {plan!.goal}</p> : null
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
            <span style={{ color: "var(--muted)", fontSize: 12 }} title="Server-minted run binding">
              run {actions.runId}
            </span>
          )}
        </div>
      )}

      {(phase === "in-flight" || phase === "done") && (
        <p style={{ color: "var(--muted)", fontSize: 13, margin: "8px 0 0" }}>
          No plan-gate action available in this state.
        </p>
      )}

      {note && (
        <p style={{ color: "var(--accent)", fontSize: 13, margin: "10px 0 0" }} role="status">
          {note}
        </p>
      )}
      {error && (
        <p style={{ color: "var(--red)", fontSize: 13, margin: "10px 0 0" }} role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
