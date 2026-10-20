"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import type { AgentPlan } from "@contracts/plan";
import type { RunStatus } from "@contracts/api";
import { loadSession } from "@/lib/auth";
import { getRunStatus, getPlan } from "@/lib/api";
import { useEventStream } from "@/lib/useEventStream";
import {
  createRefetchCoalescer,
  REFETCH_COALESCE_MS,
  type RefetchCoalescer,
} from "@/lib/backoff";
import { deriveWorkingAreaView } from "@/lib/deriveView";
import { PlanPanel } from "./PlanPanel";
import { StatusPanel } from "./StatusPanel";
import { DiffPanel } from "./DiffPanel";
import { TerminalPanel } from "./TerminalPanel";
import { CheckpointsPanel } from "./CheckpointsPanel";
import { CommentsNudges } from "./CommentsNudges";
import { Controls } from "./Controls";

export function WorkingArea({ projectId, runId, stagedPending = false }: { projectId: string; runId: string; stagedPending?: boolean }) {
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

  // H-012: coalesce event-triggered refetches — a bus burst (or reconnect
  // replay) must produce ONE REST refresh per window, not one per event.
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  const coalescerRef = useRef<RefetchCoalescer | null>(null);
  if (!coalescerRef.current) {
    coalescerRef.current = createRefetchCoalescer(REFETCH_COALESCE_MS, () =>
      refreshRef.current(),
    );
  }

  // Any new event means the REST snapshot (status/plan) may be stale - re-pull it.
  useEffect(() => {
    if (events.length > 0) coalescerRef.current?.push();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events.length]);

  useEffect(() => {
    return () => coalescerRef.current?.cancel();
  }, []);

  if (!token) return null;

  return (
    <main style={{ maxWidth: 1100, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontSize: 18, marginBottom: 16 }}>
        Working Area <span style={{ color: "var(--muted)", fontWeight: 400 }}>· run {runId}</span>
      </h1>
      <div className="working-grid">
        <div className="working-col">
          <StatusPanel status={status} derived={derived} connectionState={connectionState} />
          <PlanPanel plan={plan} />
          <DiffPanel diffText={diffText} />
          <TerminalPanel results={[]} lines={derived.terminalLines} />
        </div>
        <div className="working-col">
          <Controls
            stagedPending={stagedPending}
            runId={runId}
            token={token}
            status={status}
            onDiffChange={setDiffText}
            onActionComplete={refresh}
          />
          {/* M12-G9: snapshot list per edit batch; re-pulls as live events land. */}
          <CheckpointsPanel projectId={projectId} token={token} refreshKey={events.length} />
          <CommentsNudges authorName={authorName} />
        </div>
      </div>
    </main>
  );
}
