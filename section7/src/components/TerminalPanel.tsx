import * as React from "react";
import { panelStyle, headingStyle } from "@/lib/styles";

export interface StructuredExecResult {
  status: "passed" | "failed" | "error";
  exit_code: number;
  passed_count: number;
  failed_count: number;
  skipped_count: number;
  duration_ms: number;
  summary: string;
  diagnostics: string[];
}

const STATUS_COLOR: Record<StructuredExecResult["status"], string> = {
  passed: "#8ee5a1",
  failed: "var(--red)",
  error: "var(--red)",
};

export function TerminalPanel({
  results,
  lines = [],
}: {
  results: StructuredExecResult[];
  lines?: string[];
}) {
  return (
    <section style={panelStyle}>
      <h2 style={headingStyle}>Terminal &amp; test results</h2>
      {results.length === 0 ? (
        <p style={{ color: "var(--muted)", fontSize: 13 }}>
          No live terminal feed yet — terminal_output events from the sandbox will
          stream here in real time once an agent run starts executing commands.
        </p>
      ) : (
        <div style={{ display: "grid", gap: 8 }}>
          {results.map((r, i) => (
            <div key={i} style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }}>
              <span style={{ color: STATUS_COLOR[r.status], fontWeight: 600 }}>
                {r.status.toUpperCase()}
              </span>{" "}
              {r.summary} ({r.passed_count} passed, {r.failed_count} failed, {r.skipped_count} skipped,{" "}
              {r.duration_ms}ms)
              {r.diagnostics.length > 0 && (
                <ul style={{ margin: "4px 0 0 0", paddingLeft: 18, color: "var(--muted)" }}>
                  {r.diagnostics.map((d, j) => (
                    <li key={j}>{d}</li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
      {lines.length > 0 && (
        <pre
          tabIndex={0}
          role="region"
          aria-label="Terminal output"
          style={{
            margin: 0,
            padding: "10px 12px",
            background: "#0b1220",
            color: "#c9d4e3",
            borderRadius: 8,
            fontFamily: "ui-monospace, monospace",
            fontSize: 12,
            maxHeight: 260,
            overflowY: "auto" as const,
            whiteSpace: "pre-wrap" as const,
          }}
        >
          {lines.join("")}
        </pre>
      )}
    </section>
  );
}
