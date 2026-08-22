"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import type { AgentPlan } from "@contracts/plan";
import type { RunStatus } from "@contracts/api";
import { loadSession } from "@/lib/auth";
import { getRunStatus, getPlan } from "@/lib/api";
import { useEventStream } from "@/lib/useEventStream";
import { deriveWorkingAreaView } from "@/lib/deriveView";
import { PlanPanel } from "./PlanPanel";
import { StatusPanel } from "./StatusPanel";
import { DiffPanel } from "./DiffPanel";
import { TerminalPanel } from "./TerminalPanel";
import { CommentsNudges } from "./CommentsNudges";
import { Controls } from "./Controls";

export function WorkingArea({ projectId, runId }: { projectId: string; runId: string }) {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [authorName, setAuthorName] = useState("");
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [plan, setPlan] = useState<AgentPlan | null>(null);
  const [diffText, setDiffText] = useState("");

  useEffect(() => {
    const session = loadSession();
    if (!session) {
      router.push("/");
      return;
    }
    setToken(session.token);
    setAuthorName(session.principal.userId);
  }, [router]);

  const refresh = useCallback(() => {
    if (!token) return;
    getRunStatus(runId, token).then(setStatus).catch(() => {});
    getPlan(runId, token).then(setPlan).catch(() => {});
  }, [runId, token]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const { events, connectionState } = useEventStream(projectId, token);
  const derived = deriveWorkingAreaView(events);

  // Any new event means the REST snapshot (status/plan) may be stale - re-pull it.
  useEffect(() => {
    if (events.length > 0) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events.length]);

  if (!token) return null;

  return (
    <main style={{ maxWidth: 1100, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontSize: 18, marginBottom: 16 }}>
        Working Area <span style={{ color: "#6b7280", fontWeight: 400 }}>· run {runId}</span>
      </h1>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          <StatusPanel status={status} derived={derived} connectionState={connectionState} />
          <PlanPanel plan={plan} />
          <DiffPanel diffText={diffText} />
          <TerminalPanel results={[]} lines={derived.terminalLines} />
        </div>
        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          <Controls
            runId={runId}
            token={token}
            status={status}
            onDiffChange={setDiffText}
            onActionComplete={refresh}
          />
          <CommentsNudges authorName={authorName} />
        </div>
      </div>
    </main>
  );
}
