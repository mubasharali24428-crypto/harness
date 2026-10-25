"use client";

// Signed-in dashboard surface, extracted from app/page.tsx so the route can
// stay a two-branch decision (landing vs app) and Next's PageProps contract
// stays untouched. Same markup and hooks as before the extraction.

import { useEffect, useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too.
import * as React from "react";
import { PlanGateCard } from "../../app/dashboard/planGate";
import AppShell from "@/components/AppShell";
import { getRunStatus, getPlan, replayEvents, listProjects } from "@/lib/api";
import { useEventStream } from "@/lib/useEventStream";
import type { ConnectionState } from "@/lib/useEventStream";
import { getToken, getProjectId, getRunId, loadSession } from "@/lib/auth";
import type { EventEnvelope } from "@contracts/events";
import type { AgentPlan } from "@contracts/plan";
import type { RunStatus } from "@contracts/api";
// M16-X4 (raw-17): markup migrated onto the shared DS primitives — Card
// replaces the class-only shells and the tinted error line moved onto the
// proposed Notice primitive. Visual parity intent; the .pill class hooks and
// data-testid="live-pill" are preserved verbatim.
import { Card, Pill } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";

export function CommandCenterView() {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [plan, setPlan] = useState<AgentPlan | null>(null);
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [projectName, setProjectName] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  // H-012: the live pill is driven by the WS connection lifecycle, not by REST
  // fetches succeeding. `source` is now derived purely from that state.
  const { connectionState, attempt } = useEventStream(
    typeof window !== "undefined" ? getProjectId() : null,
    typeof window !== "undefined" ? getToken() : null,
  );

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    const projectId = getProjectId();
    const runId = getRunId();

    (async () => {
      try {
        if (projectId) {
          const projects = await listProjects(token);
          setProjectName(projects.find((p) => p.id === projectId)?.name ?? projectId);
        }
      } catch {
        /* project name is cosmetic */
      }
      try {
        if (runId) {
          const [st, pl] = await Promise.allSettled([
            getRunStatus(runId, token),
            getPlan(runId, token),
          ]);
          if (st.status === "fulfilled") {
            setStatus(st.value);
          }
          if (pl.status === "fulfilled" && pl.value) setPlan(pl.value);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
      try {
        if (projectId) {
          const { items } = await replayEvents(projectId, 0, token);
          // newest last for display consistency with the working area
          setEvents(items.slice(-50));
        }
      } catch {
        /* replay needs a real project; idle until one exists */
      }
    })();
  }, []);

  // H-012: pill state machine — WS-truthful. REST snapshots no longer flip the
  // pill; only the actual event-stream lifecycle does. M16-X4: the DS tone
  // rides alongside the legacy class so <Pill> renders the identical face.
  const PILL: Record<ConnectionState, { label: string; cls: string; tone: "yellow" | "green" | "red" }> = {
    connecting: { label: "connecting", cls: "yellow", tone: "yellow" },
    open: { label: "live", cls: "green", tone: "green" },
    reconnecting: { label: `reconnecting (attempt ${attempt})`, cls: "yellow", tone: "yellow" },
    closed: { label: "offline", cls: "red", tone: "red" },
  };
  const pill = PILL[connectionState];

  return (
    <AppShell active="/">
      <h1>
        Command Center{projectName ? ` — ${projectName}` : ""}{" "}
        <Pill tone={pill.tone} className={`pill ${pill.cls}`} testId="live-pill">
          {pill.label}
        </Pill>
      </h1>
      <PlanGateCard token={getToken()!} principal={loadSession()?.principal ?? null} />
      <div className="grid">
        {/* M16-X4: status cards onto Card; the .pill spans keep their class
            hooks (live-pill testid + e2e selectors) verbatim. */}
        <Card title="Agent Status">
          {status ? (
            <>
              <div className="status-line">
                <span>{getRunId() ?? "—"}</span>
                <Pill
                  tone={status.state === "executing" ? "blue" : "yellow"}
                  className={`pill ${status.state === "executing" ? "blue" : "yellow"}`}
                >
                  {status.state}
                </Pill>
              </div>
              <div className="status-line">
                <span>plan approved</span>
                <span>{status.planApproved ? "✓" : "pending"}</span>
              </div>
              {plan && (
                <div className="status-line">
                  <span>est. cost</span>
                  <span>${(plan.estimatedCostUsd ?? 0).toFixed(3)}</span>
                </div>
              )}
            </>
          ) : (
            <p>No live run yet — start one in the Working Area after approving a plan.</p>
          )}
        </Card>

        <Card title="Current Plan">
          {plan ? (
            <>
              <ul>
                {(plan.steps ?? []).map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
              <div className="status-line">
                <span>est. cost</span>
                <span>${(plan.estimatedCostUsd ?? 0).toFixed(3)}</span>
              </div>
              <div className="status-line">
                <span>forbidden paths</span>
                <span>{(plan.forbiddenPaths ?? []).join(", ") || "none"}</span>
              </div>
            </>
          ) : (
            <p>No plan proposed for the current run.</p>
          )}
        </Card>

        <Card className="card event-feed" title="Activity Stream">
          {events.length === 0 && <p>No events yet.</p>}
          {[...events].reverse().map((e) => (
            <div className="event-row" key={e.seq}>
              <span className="event-ts">{new Date(e.ts).toLocaleTimeString()}</span>
              <span className="event-type">{e.type}</span>
              <span>{e.runId}</span>
            </div>
          ))}
        </Card>

        <Card title="Guardrails">
          <div className="status-line">
            <span>auto-merge</span>
            <Pill tone="red" className="pill red">never</Pill>
          </div>
          <div className="status-line">
            <span>plan gate</span>
            <Pill tone="green" className="pill green">enforced</Pill>
          </div>
          <div className="status-line">
            <span>sandbox</span>
            <Pill tone="green" className="pill green">allow-list + OS confinement</Pill>
          </div>
          <div className="status-line">
            <span>forbidden paths</span>
            <span>.env*, infra/</span>
          </div>
        </Card>
      </div>
      {error && <Notice tone="danger">{error}</Notice>}
    </AppShell>
  );
}
