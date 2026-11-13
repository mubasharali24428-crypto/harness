"use client";

// Signed-in dashboard surface. Chat-app shape rather than a card dashboard:
// a composer at the top, the plan it produced, then a chronological run
// timeline, with the guardrails reduced to one quiet strip. Extracted from
// app/page.tsx so the route stays a two-branch decision.

import { useEffect, useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too.
import * as React from "react";
import { PlanGateCard } from "../../app/dashboard/planGate";
import AppShell from "@/components/AppShell";
import HomeTriage from "@/components/HomeTriage";
import { getRunStatus, getPlan, replayEvents, listProjects } from "@/lib/api";
import { useEventStream } from "@/lib/useEventStream";
import type { ConnectionState } from "@/lib/useEventStream";
import { getToken, getProjectId, getRunId, loadSession } from "@/lib/auth";
import { eventLabel, eventTone } from "@/lib/eventLabels";
import type { EventEnvelope } from "@contracts/events";
import type { AgentPlan } from "@contracts/plan";
import type { RunStatus } from "@contracts/api";
import { Card, Pill } from "@/components/ui";
import { Notice } from "@/components/ui/Notice";
import { cssVar, fontSize, radius, space } from "./tokens";

const page: React.CSSProperties = { maxWidth: 900, margin: "0 auto" };
const titleRow: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "space-between",
  gap: space.md,
  flexWrap: "wrap",
  marginBottom: space.xl,
};
const sectionHead: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: space.md,
  flexWrap: "wrap",
  marginBottom: space.md,
};
const monoMeta: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: fontSize.xs,
  color: cssVar("muted"),
};
const TONE_DOT = {
  danger: cssVar("red"),
  success: cssVar("green"),
  accent: cssVar("accent"),
} as const;

const STEPS = [
  "Describe the goal — the agent proposes a plan with steps, tests and risks.",
  "Approve or reject it. Nothing executes before a person decides.",
  "Follow the run here: every engine event lands in the timeline below.",
];

export function CommandCenterView() {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [plan, setPlan] = useState<AgentPlan | null>(null);
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [projectName, setProjectName] = useState<string>("");
  const [projectNames, setProjectNames] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

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
          setProjectNames(Object.fromEntries(projects.map((p) => [p.id, p.name])));
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
          if (st.status === "fulfilled") setStatus(st.value);
          if (pl.status === "fulfilled" && pl.value) setPlan(pl.value);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
      try {
        if (projectId) {
          const { items } = await replayEvents(projectId, 0, token);
          setEvents(items.slice(-50));
        }
      } catch {
        /* replay needs a real project; idle until one exists */
      }
    })();
  }, []);

  const PILL: Record<ConnectionState, { label: string; cls: string; tone: "yellow" | "green" | "red" }> = {
    connecting: { label: "connecting", cls: "yellow", tone: "yellow" },
    open: { label: "live", cls: "green", tone: "green" },
    reconnecting: { label: `reconnecting (attempt ${attempt})`, cls: "yellow", tone: "yellow" },
    closed: { label: "offline", cls: "red", tone: "red" },
  };
  const pill = PILL[connectionState];

  return (
    <AppShell active="/">
      <div style={page}>
        <div style={titleRow}>
          <div>
            <h1 style={{ margin: 0 }}>
              Command Center{projectName ? ` — ${projectName}` : ""}
            </h1>
            <p style={{ margin: "6px 0 0", color: cssVar("muted"), fontSize: fontSize.md }}>
              Describe the goal. The agent plans, you approve, every step is checkable.
            </p>
          </div>
          <Pill tone={pill.tone} className={`pill ${pill.cls}`} testId="live-pill">
            {pill.label}
          </Pill>
        </div>

        <HomeTriage projectNames={projectNames} />

        <PlanGateCard token={getToken()!} principal={loadSession()?.principal ?? null} />

        {plan && (
          <Card title="Current plan" style={{ marginTop: space.xl }}>
            <>
              <div style={sectionHead}>
                <span style={monoMeta}>
                  {(plan.steps ?? []).length} steps · est. ${(plan.estimatedCostUsd ?? 0).toFixed(3)}
                </span>
                {(plan.forbiddenPaths ?? []).length > 0 && (
                  <span style={monoMeta}>forbidden: {(plan.forbiddenPaths ?? []).join(", ")}</span>
                )}
              </div>
              <ol
                style={{
                  margin: 0,
                  paddingLeft: 20,
                  display: "grid",
                  gap: 6,
                  color: cssVar("text2"),
                  fontSize: fontSize.md,
                }}
              >
                {(plan.steps ?? []).map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ol>
            </>
          </Card>
        )}

        <Card
          title={`Activity${status ? ` · ${status.state.replaceAll("_", " ")}` : ""}`}
          className="card event-feed"
          style={{ marginTop: space.xl }}
        >
          {getRunId() && <p style={{ ...monoMeta, margin: "0 0 10px" }}>run {getRunId()}</p>}
          {events.length === 0 ? (
            <div style={{ padding: "18px 0 8px" }}>
              <p style={{ margin: "0 0 10px", color: cssVar("text2"), fontSize: fontSize.md }}>
                Nothing has happened yet.
              </p>
              <ol style={{ margin: 0, paddingLeft: 18, color: cssVar("muted"), fontSize: fontSize.sm, lineHeight: 1.8 }}>
                {STEPS.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ol>
            </div>
          ) : (
            <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {events.map((e) => (
                <li
                  key={e.seq}
                  style={{
                    display: "flex",
                    gap: 10,
                    alignItems: "flex-start",
                    padding: "9px 0",
                    borderBottom: `1px solid ${cssVar("borderSoft")}`,
                  }}
                >
                  <span
                    aria-hidden="true"
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: radius.pill,
                      background: TONE_DOT[eventTone(String(e.type))],
                      marginTop: 6,
                      flex: "none",
                    }}
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                      <span style={{ fontSize: fontSize.md }}>{eventLabel(String(e.type))}</span>
                      <time style={monoMeta}>{new Date(e.ts).toLocaleTimeString()}</time>
                    </div>
                    <div style={{ ...monoMeta, overflowWrap: "anywhere" }}>{e.type}</div>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </Card>

        <section
          aria-label="Guardrails"
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: `${space.sm}px ${space.block}px`,
            alignItems: "center",
            marginTop: space.xl,
            padding: `${space.md}px ${space.lg}px`,
            border: `1px solid ${cssVar("borderSoft")}`,
            borderRadius: radius.lg,
            color: cssVar("muted"),
            fontSize: fontSize.xs,
          }}
        >
          {[
            "auto-merge: never",
            "plan approval: required",
            "sandbox: allow-list + OS confinement",
            "provider keys: yours",
          ].map((g) => (
            <code key={g} style={{ fontFamily: "var(--font-mono)", color: cssVar("muted") }}>
              {g}
            </code>
          ))}
        </section>

        {error && (
          <div style={{ marginTop: space.xl }}>
            <Notice tone="danger">{error}</Notice>
          </div>
        )}
      </div>
    </AppShell>
  );
}
