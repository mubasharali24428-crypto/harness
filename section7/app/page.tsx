"use client";

import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { getRunStatus, getPlan, replayEvents, listProjects } from "@/lib/api";
import {
  getToken,
  getProjectId,
  getRunId,
} from "@/lib/auth";
import type { EventEnvelope } from "@contracts/events";
import type { AgentPlan } from "@contracts/plan";
import type { RunStatus } from "@contracts/api";

export default function CommandCenter() {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [plan, setPlan] = useState<AgentPlan | null>(null);
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [projectName, setProjectName] = useState<string>("");
  const [source, setSource] = useState<"live" | "idle">("idle");
  const [error, setError] = useState<string | null>(null);

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
            setSource("live");
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
          if (items.length) setSource("live");
        }
      } catch {
        /* replay needs a real project; idle until one exists */
      }
    })();
  }, []);

  const last = events[events.length - 1];

  return (
    <AppShell active="/">
      <h1>
        Command Center{projectName ? ` — ${projectName}` : ""}{" "}
        <span className={`pill ${source === "live" ? "green" : "yellow"}`}>{source}</span>
      </h1>
      {!getToken() && (
        <div className="card">
          <h2>Welcome</h2>
          <p>
            Not signed in yet.{" "}
            <a href="/login" style={{ color: "var(--accent)" }}>
              Go to login →
            </a>
          </p>
        </div>
      )}
      <div className="grid">
        <div className="card">
          <h2>Agent Status</h2>
          {status ? (
            <>
              <div className="status-line">
                <span>{getRunId() ?? "—"}</span>
                <span className={`pill ${status.state === "executing" ? "blue" : "yellow"}`}>
                  {status.state}
                </span>
              </div>
              <div className="status-line">
                <span>plan approved</span>
                <span>{status.planApproved ? "✓" : "pending"}</span>
              </div>
              {plan && (
                <div className="status-line">
                  <span>est. cost</span>
                  <span>${plan.estimatedCostUsd.toFixed(3)}</span>
                </div>
              )}
            </>
          ) : (
            <p>No live run yet — start one in the Working Area after approving a plan.</p>
          )}
        </div>

        <div className="card">
          <h2>Current Plan</h2>
          {plan ? (
            <>
              <ul>
                {plan.steps.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
              <div className="status-line">
                <span>est. cost</span>
                <span>${plan.estimatedCostUsd.toFixed(3)}</span>
              </div>
              <div className="status-line">
                <span>forbidden paths</span>
                <span>{plan.forbiddenPaths.join(", ") || "none"}</span>
              </div>
            </>
          ) : (
            <p>No plan proposed for the current run.</p>
          )}
        </div>

        <div className="card event-feed">
          <h2>Activity Stream</h2>
          {events.length === 0 && <p>No events yet.</p>}
          {[...events].reverse().map((e) => (
            <div className="event-row" key={e.seq}>
              <span className="event-ts">{new Date(e.ts).toLocaleTimeString()}</span>
              <span className="event-type">{e.type}</span>
              <span>{e.runId}</span>
            </div>
          ))}
        </div>

        <div className="card">
          <h2>Guardrails</h2>
          <div className="status-line">
            <span>auto-merge</span>
            <span className="pill red">never</span>
          </div>
          <div className="status-line">
            <span>plan gate</span>
            <span className="pill green">enforced</span>
          </div>
          <div className="status-line">
            <span>sandbox</span>
            <span className="pill green">allow-list + OS confinement</span>
          </div>
          <div className="status-line">
            <span>forbidden paths</span>
            <span>.env*, infra/</span>
          </div>
        </div>
      </div>
      {error && <p style={{ color: "var(--red)" }}>{error}</p>}
    </AppShell>
  );
}
