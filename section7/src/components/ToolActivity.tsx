import * as React from "react";
import { panelStyle, headingStyle, buttonStyle } from "@/lib/styles";
import type { ToolCallView } from "@/lib/deriveView";

const STATE_COLOR = {
  ok: "var(--green)",
  error: "var(--red)",
  running: "var(--blue)",
} as const;

function stateOf(call: ToolCallView): keyof typeof STATE_COLOR {
  if (call.ok === null) return "running";
  return call.ok ? "ok" : "error";
}

function duration(call: ToolCallView): string {
  if (call.durationMs === null) return "";
  return call.durationMs < 1000 ? `${call.durationMs}ms` : `${(call.durationMs / 1000).toFixed(1)}s`;
}

/** One row per tool invocation, in call order. A call with no result yet reads
 *  "running" — the panel never reports an outcome it has not received. */
export function ToolActivity({ calls, onEditScope }: {
  calls: ToolCallView[];
  /** Opens the scope editor. Without a handler the prompt is read-only. */
  onEditScope?: (path: string) => void;
}) {
  return (
    <section style={panelStyle} aria-label="Tool activity">
      <h2 style={headingStyle}>Tool activity</h2>
      {calls.length === 0 ? (
        <p style={{ color: "var(--muted)", fontSize: 13 }}>
          No tool calls yet. Each file read, search and command appears here as it happens.
        </p>
      ) : (
        <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
          {calls.map((call, i) => {
            const state = stateOf(call);
            const took = duration(call);
            const scopeRequest = call.scopeRequest;
            return (
              <li key={`${call.callId}-${i}`} style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
                  <span aria-hidden="true" style={{ color: "var(--muted)", minWidth: 20 }}>
                    {i + 1}.
                  </span>
                  <span style={{ fontWeight: 600 }}>{call.name}</span>
                  <span style={{ color: STATE_COLOR[state] }}>{state}</span>
                  {took !== "" && <span style={{ color: "var(--muted)" }}>{took}</span>}
                </div>
                {call.arguments !== "" && (
                  <details style={{ marginLeft: 28, color: "var(--muted)" }}>
                    <summary style={{ cursor: "pointer" }}>input</summary>
                    <pre
                      style={{
                        margin: "4px 0 0",
                        whiteSpace: "pre-wrap" as const,
                        wordBreak: "break-word" as const,
                        fontSize: 11,
                      }}
                    >
                      {call.arguments}
                    </pre>
                  </details>
                )}
                {call.error !== null && (
                  <p style={{ margin: "2px 0 0 28px", color: "var(--red)" }}>{call.error}</p>
                )}
                {call.output !== "" && (
                  <details style={{ marginLeft: 28, color: "var(--muted)" }}>
                    <summary style={{ cursor: "pointer" }}>output</summary>
                    <pre
                      style={{
                        margin: "4px 0 0",
                        whiteSpace: "pre-wrap" as const,
                        wordBreak: "break-word" as const,
                        fontSize: 11,
                      }}
                    >
                      {call.output}
                    </pre>
                  </details>
                )}
                {scopeRequest !== null && (
                  <p data-testid="scope-request" style={{ margin: "2px 0 0 28px", color: "var(--muted)" }}>
                    needs scope: {scopeRequest.path}{" "}
                    {onEditScope && (
                      <button
                        type="button"
                        data-testid="scope-edit-open"
                        style={{ ...buttonStyle, padding: "2px 8px", fontSize: 11 }}
                        onClick={() => onEditScope(scopeRequest.path)}
                      >
                        Edit scope
                      </button>
                    )}
                  </p>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
