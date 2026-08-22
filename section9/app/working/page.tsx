"use client";

import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import CodeEditor from "@/components/CodeEditor";
import { api, orchestrator } from "@/lib/api";
import { mockEvents, mockPlan } from "@/lib/mockApi";
import type { AgentPlan, EventEnvelope } from "@contracts";

type Source = "live" | "mock";

export default function Working() {
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [plan, setPlan] = useState<AgentPlan>(mockPlan);
  const [source, setSource] = useState<Source>("mock");
  const [runId, setRunId] = useState(() => {
    // fresh run per session: the orchestrator keeps per-run state machines
    return `run-${Math.random().toString(36).slice(2, 8)}`;
  });
  const [planId, setPlanId] = useState<string | null>(null);
  const [stateLabel, setStateLabel] = useState("idle");
  const [engineOut, setEngineOut] = useState<string>("");
  const [engineStatus, setEngineStatus] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const [manualEdit, setManualEdit] = useState(
    'def health():\n    return {"ok": True, "status": "up"}\n'
  );
  const [reviewResult, setReviewResult] = useState<{ passed: boolean; summary: string } | null>(null);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const say = (m: string) => setLog((l) => [...l.slice(-8), m]);

  const guard = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      say(`ERROR: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    const projectId = localStorage.getItem("harness.projectId") ?? "proj-test-1";
    localStorage.setItem("harness.runId", runId);
    (async () => {
      if (!api.getToken()) return;
      if (!api.getToken()) return;
      try {
        const [ev] = await Promise.allSettled([api.replayEvents(projectId)]);
        if (ev.status === "fulfilled" && ev.value.length) {
          setEvents(ev.value);
          setSource("live");
        }
      } catch {
        /* mocks */
      }
    })();
  }, []);

  const refreshEngine = () =>
    guard(async () => {
      const r = await orchestrator.engineResult<{
        status: string;
        finalResponse: string;
        engine?: string;
      }>(runId);
      setEngineStatus(`${r.status}${r.engine ? ` · ${r.engine}` : ""}`);
      setEngineOut(r.finalResponse ?? "");
    });

  return (
    <AppShell active="/working">
      <h1>
        Working Area — <code>{runId}</code>{" "}
        <span className={`pill ${source === "live" ? "green" : "yellow"}`}>{source}</span>{" "}
        <span className="pill blue">state: {stateLabel}</span>
      </h1>
      <div className="grid">
        <div className="card">
          <h2>Lifecycle (real calls → S4)</h2>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                guard(async () => {
                  const goal =
                    localStorage.getItem("harness.goal") ??
                    "In the workspace, create hello.py containing a function ok() that returns the string 'ok'.";
                  const r = await orchestrator.proposePlan(runId, goal);
                  setPlanId(r.planId);
                  setStateLabel("awaiting_plan_approval");
                  say(`plan proposed: ${r.planId}`);
                })
              }
            >
              1. Propose plan
            </button>
            <button
              className="btn"
              disabled={busy || !planId}
              onClick={() =>
                guard(async () => {
                  await orchestrator.approvePlan(runId, planId!);
                  setStateLabel("approved");
                  say("plan approved ✓ (gate satisfied)");
                })
              }
            >
              2. Approve plan
            </button>
            <button
              className="btn primary"
              disabled={busy || !planId}
              onClick={() =>
                guard(async () => {
                  await orchestrator.start(runId);
                  setStateLabel("executing");
                  say("execution started — agent is running for real…");
                  setTimeout(refreshEngine, 3000);
                })
              }
            >
              3. Start execution
            </button>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
            <button className="btn danger" disabled={busy} onClick={() => guard(async () => { await orchestrator.pause(runId); setStateLabel("paused"); say("paused"); })}>
              Pause
            </button>
            <button className="btn" disabled={busy} onClick={() => guard(async () => { await orchestrator.resume(runId); setStateLabel("executing"); say("resumed"); })}>
              Resume
            </button>
            <button className="btn danger" disabled={busy} onClick={() => guard(async () => { await orchestrator.stop(runId); setStateLabel("stopped"); say("stopped"); })}>
              Stop
            </button>
            <button
              className="btn primary"
              disabled={busy}
              onClick={() =>
                guard(async () => {
                  const r = await orchestrator.doneSignal(runId, manualEdit);
                  setReviewResult(r.findings);
                  setStateLabel("reviewed");
                  say(`done-signal → reviewer: ${r.findings.passed ? "PASS" : "FAIL"}`);
                })
              }
            >
              Done — send to reviewer
            </button>
          </div>
          {reviewResult && (
            <p style={{ marginTop: 10 }}>
              Reviewer verdict:{" "}
              <span className={`pill ${reviewResult.passed ? "green" : "red"}`}>
                {reviewResult.passed ? "PASSED" : "FAILED"}
              </span>{" "}
              — {reviewResult.summary}
            </p>
          )}
        </div>

        <div className="card event-feed">
          <h2>Action log</h2>
          {log.map((l, i) => (
            <div className="event-row" key={i}>
              <span className="event-type">{l}</span>
            </div>
          ))}
        </div>

        <div className="card event-feed">
          <h2>Agent Events</h2>
          {[...events].reverse().map((e) => (
            <div className="event-row" key={e.seq}>
              <span className="event-ts" suppressHydrationWarning>
                {mounted ? new Date(e.ts).toLocaleTimeString() : ""}
              </span>
              <span className="event-type">{e.type}</span>
            </div>
          ))}
        </div>

        <div className="card">
          <h2>Plan {planId ? `(${planId.slice(0, 14)}…)` : "(propose to fetch)"}</h2>
          <ul>{plan.steps.map((s) => <li key={s}>{s}</li>)}</ul>
        </div>

        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h2>
            Engine Output{" "}
            {engineStatus && (
              <span className={`pill ${engineStatus === "completed" ? "green" : "yellow"}`}>
                {engineStatus}
              </span>
            )}
          </h2>
          <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>
            {engineOut || "Start execution to see the live DeepSeek Harness response here."}
          </pre>
          <button className="btn" disabled={busy} onClick={refreshEngine} style={{ marginTop: 8 }}>
            Refresh engine output
          </button>
        </div>

        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h2>Human Takeover Edit (implicit pause while editing)</h2>
          <CodeEditor value={manualEdit} onChange={setManualEdit} />
        </div>
      </div>
    </AppShell>
  );
}

