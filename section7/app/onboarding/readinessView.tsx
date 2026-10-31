"use client";

// W23 — first-run readiness: the orchestrator's own verdict on whether a run
// started now in this project would work, one row per check, naming the
// component at fault. Presentational only (rendered by vitest with
// react-dom/server, like checklistView.tsx); the page owns the fetch.
import * as React from "react";
import type { ReadinessCheck, ReadinessReport } from "@/lib/api";

const LABELS: Record<ReadinessCheck["status"], { text: string; pill: string; icon: string }> = {
  ok: { text: "OK", pill: "pill green", icon: "✅" },
  fail: { text: "Failing", pill: "pill red", icon: "❌" },
  unknown: { text: "Unknown", pill: "pill blue", icon: "—" },
};

export function ReadinessCard({
  projectId,
  report,
  error,
}: {
  projectId: string | null;
  report: ReadinessReport | null;
  error: string | null;
}) {
  return (
    <section className="card" style={{ maxWidth: 760, marginBottom: 20 }} data-testid="readiness-card">
      <h2>Run readiness</h2>
      <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 0 }}>
        Checked by the orchestrator for the current project: provider, model, worker, code
        revision, repository and your role. Unknown never counts as a pass.
      </p>
      {projectId === null && (
        <p data-testid="readiness-no-project" style={{ color: "var(--muted)" }}>
          Pick a project in the Command Center first; readiness is checked per project.
        </p>
      )}
      {error && (
        <p role="alert" style={{ color: "var(--red)" }}>
          Readiness could not be read: {error}
        </p>
      )}
      {report && (
        <>
          <p data-testid="readiness-verdict" style={{ fontWeight: 600 }}>
            {report.ok ? "Ready to run" : "Not ready: fix the failing checks below"}
          </p>
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {report.checks.map((c) => {
              const label = LABELS[c.status] ?? LABELS.unknown;
              return (
                <li
                  key={c.check}
                  data-testid={`readiness-check-${c.check}`}
                  style={{ padding: "6px 0", borderBottom: "1px solid var(--border)", fontSize: 13 }}
                >
                  <span aria-hidden="true">{label.icon}</span> <strong>{c.check}</strong>{" "}
                  <span className={label.pill}>{label.text}</span>{" "}
                  <span style={{ color: "var(--muted)" }}>
                    {c.component}: {c.detail}
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
