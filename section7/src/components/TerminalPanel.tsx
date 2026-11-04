import * as React from "react";
import { panelStyle, headingStyle } from "@/lib/styles";

/** Mirror of `harness_sandbox.output_parser.Diagnostic` — the Hermes
 *  `{path,line,col,kind,message}` record. Positions are optional because the
 *  parser only reports what the tool actually said (see `diagnostics_source`). */
export interface Diagnostic {
  path?: string | null;
  line?: number | null;
  col?: number | null;
  kind: "error" | "warning" | "note";
  message: string;
}

export interface StructuredExecResult {
  status: "passed" | "failed" | "error";
  exit_code: number;
  passed_count: number;
  failed_count: number;
  skipped_count: number;
  duration_ms: number;
  summary: string;
  diagnostics: Diagnostic[];
  /** protocol = the tool emitted structured diagnostics; located = positions
   *  parsed from compiler-style text; scan = keyword match, no positions. */
  diagnostics_source?: "protocol" | "located" | "scan" | "none";
  diagnostics_truncated?: boolean;
}

const STATUS_COLOR: Record<StructuredExecResult["status"], string> = {
  passed: "var(--green)",
  failed: "var(--red)",
  error: "var(--red)",
};

const KIND_COLOR: Record<Diagnostic["kind"], string> = {
  error: "var(--red)",
  warning: "var(--yellow)",
  note: "var(--muted)",
};

const SOURCE_NOTE: Record<NonNullable<StructuredExecResult["diagnostics_source"]>, string> = {
  protocol: "",
  located: "",
  scan: "This tool reports no file or line, so these are matches from its output.",
  none: "",
};

function position(d: Diagnostic): string {
  if (d.path == null) return "";
  if (d.line == null) return d.path;
  return d.col == null ? `${d.path}:${d.line}` : `${d.path}:${d.line}:${d.col}`;
}

export function TerminalPanel({
  results,
  lines = [],
}: {
  results: StructuredExecResult[];
  lines?: string[];
}) {
  const hasTranscript = lines.length > 0;
  return (
    <section style={panelStyle}>
      <h2 style={headingStyle}>Terminal &amp; test results</h2>
      {results.length === 0 && !hasTranscript && (
        <p style={{ color: "var(--muted)", fontSize: 13 }}>
          Nothing has run yet. Command output and test results appear here while
          the agent works.
        </p>
      )}
      {results.length > 0 && (
        <div style={{ display: "grid", gap: 8 }}>
          {results.map((r, i) => {
            const note = SOURCE_NOTE[r.diagnostics_source ?? "none"];
            return (
              <div key={i} style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }}>
                <span style={{ color: STATUS_COLOR[r.status], fontWeight: 600 }}>
                  {r.status.toUpperCase()}
                </span>{" "}
                {r.summary} ({r.passed_count} passed, {r.failed_count} failed, {r.skipped_count}{" "}
                skipped, {r.duration_ms}ms)
                {r.diagnostics.length > 0 && (
                  <ul style={{ margin: "4px 0 0 0", paddingLeft: 18, color: "var(--muted)" }}>
                    {r.diagnostics.map((d, j) => {
                      const where = position(d);
                      return (
                        <li key={j}>
                          {where !== "" && (
                            <span style={{ color: "var(--muted)" }}>{where} </span>
                          )}
                          <span style={{ color: KIND_COLOR[d.kind], fontWeight: 600 }}>
                            {d.kind}
                          </span>{" "}
                          {d.message}
                        </li>
                      );
                    })}
                  </ul>
                )}
                {(note !== "" || r.diagnostics_truncated) && (
                  <p style={{ color: "var(--muted)", fontSize: 11, margin: "4px 0 0 0" }}>
                    {[
                      r.diagnostics_truncated ? "Showing the first 20." : "",
                      note,
                    ]
                      .filter(Boolean)
                      .join(" ")}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
      {hasTranscript && (
        <>
          <p style={{ color: "var(--muted)", fontSize: 12, margin: results.length > 0 ? "10px 0 4px" : 0 }}>
            {lines.length} line{lines.length === 1 ? "" : "s"}
            {lines.length >= 200 ? " (latest 200 kept)" : ""}
          </p>
          {/* The transcript stays a dark surface in both themes on purpose —
              it is a terminal, and mixing it with paper text costs legibility
              of ANSI-style output. */}
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
        </>
      )}
    </section>
  );
}
