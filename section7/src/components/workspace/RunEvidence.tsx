"use client";

// R5 evidence: the run's cost as one number, its recorded events as a
// waterfall, and an export of the run history. Data is the run aggregate the
// page already holds; the export is the orchestrator's existing export job.

import * as React from "react";
import { Button } from "@/components/ui";
import { loadErrorMessage } from "@/lib/humanize";
import { formatCostUsdExact, type RunAggregateDto } from "../../../app/runs/runsApi";
import { PLAN_GATE_BASE } from "../../../app/dashboard/planGateApi";
import { call, post } from "../../../app/projects/[projectId]/runs/[runId]/collabApi";
import { buildTrace, formatMs } from "./trace";

interface ExportJob {
  id: string;
  state: string;
  format: string;
  errorCode: string | null;
  recordCount: number | null;
}

const EXPORTS = `${PLAN_GATE_BASE}/api/v1/orchestrator/exports`;
const isNum = (v: number | null): v is number => typeof v === "number" && Number.isFinite(v);

export function RunEvidence({ agg, token }: { agg: RunAggregateDto; token: string }) {
  const trace = React.useMemo(() => buildTrace(agg.steps), [agg.steps]);
  const [busy, setBusy] = React.useState(false);
  const [note, setNote] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  async function exportRun() {
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      let job = await post<ExportJob>(EXPORTS, token, {
        kind: "run_history", scopeId: agg.runId, format: "json", requestId: crypto.randomUUID(),
      });
      // ponytail: poll for up to 20 s; a longer export stays listed in the Plan pane's exports.
      for (let i = 0; i < 20 && job.state === "running"; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        job = await call<ExportJob>(`${EXPORTS}/${encodeURIComponent(job.id)}`, token);
      }
      if (job.state !== "completed") {
        setNote(job.state === "running"
          ? "The export is still being prepared. Download it from the Plan pane when it is ready."
          : `The export did not complete (${job.errorCode ?? job.state}).`);
        return;
      }
      const res = await fetch(`${EXPORTS}/${encodeURIComponent(job.id)}/download`, { headers: { authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`Download failed (${res.status})`);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `harness-run-${agg.runId}.${job.format}`;
      a.click();
      URL.revokeObjectURL(url);
      setNote(`Exported ${job.recordCount ?? "the"} records of this run.`);
    } catch (e) {
      setError(loadErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="ws-evidence" aria-label="Cost and trace" data-testid="run-evidence">
      <p className="ws-evidence-cost" data-testid="evidence-cost">
        <strong>{isNum(agg.costUsd) ? formatCostUsdExact(agg.costUsd) : "Not metered yet"}</strong>
        <span>
          {isNum(agg.costUsd) ? "spent on this run (metered)" : "no spend has been recorded for this run"}
          {" · "}
          {isNum(agg.estimatedCostUsd) ? `plan estimate ${formatCostUsdExact(agg.estimatedCostUsd)}` : "no plan estimate"}
        </span>
      </p>

      <h3>Trace</h3>
      {trace.rows.length === 0 ? (
        <p className="ws-evidence-note">No timed events are recorded for this run yet.</p>
      ) : (
        <>
          <p className="ws-evidence-note">
            {trace.rows.length} events over {formatMs(trace.totalMs)}. A bar is the time until the next recorded
            event, unless the server measured the step itself.
            {trace.untimed > 0 ? ` ${trace.untimed} events have no recorded time and are left out.` : ""}
            {agg.steps.length >= 100 ? " Only the latest 100 events are loaded." : ""}
          </p>
          <ol className="ws-trace" data-testid="run-trace">
            {trace.rows.map((r) => (
              <li key={r.seq}>
                <span className="ws-trace-label">{r.label}</span>
                <span className="ws-trace-time">{formatMs(r.durationMs)}{r.measured ? " measured" : ""}</span>
                <span className="ws-trace-track" aria-hidden="true">
                  <span className={`ws-trace-bar${r.measured ? " measured" : ""}`} style={{ marginLeft: `${r.leftPct}%`, width: `${Math.min(r.widthPct, 100 - r.leftPct)}%` }} />
                </span>
              </li>
            ))}
          </ol>
        </>
      )}

      <div className="ws-evidence-export">
        <Button size="sm" disabled={busy} onClick={() => void exportRun()} testId="evidence-export">
          {busy ? "Preparing the export…" : "Export run history (JSON)"}
        </Button>
        {note && <span role="status">{note}</span>}
        {error && <span role="alert">{error}</span>}
      </div>
    </section>
  );
}
