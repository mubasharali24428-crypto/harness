"use client";

// W28 S1b-2 — read-only review of a run's delegated children. The one request
// is GET /runs/{runId}/synthesis through controlApi.getSynthesis. There is no
// button, form, or any other path here that can merge, approve or change a run.
import * as React from "react";
import { useEffect, useState } from "react";
import { type SynthesisView, getSynthesis } from "./controlApi";

export type SynthesisState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; view: SynthesisView };

const REVIEW_LINE = "Review required. Nothing is merged automatically.";
const muted = { color: "var(--muted)" } as const;
const cell = {
  padding: "4px 8px",
  borderBottom: "1px solid var(--border)",
  textAlign: "left" as const,
};

/** Pure view. Every state is said in words; a null result is never a blank cell,
 *  and spentUsd is the exact string the API sent, including a real "0". */
export function SynthesisPanel({ state }: { state: SynthesisState }) {
  const view = state.kind === "ready" ? state.view : null;
  const children = view?.children ?? [];
  const titles = new Map(children.map((c) => [c.assignmentId, c.title]));
  return (
    <section className="card" aria-label="Delegated synthesis" data-testid="synthesis-panel"
      style={{ maxWidth: 980, marginBottom: 20 }}>
      <h2 style={{ margin: "0 0 8px", fontSize: 14 }}>Delegated children</h2>
      <p data-testid="synthesis-review-line" style={{ fontSize: 13, margin: "0 0 8px" }}>
        {REVIEW_LINE}
      </p>

      {state.kind === "loading" && (
        <p data-testid="synthesis-loading" style={{ ...muted, fontSize: 13 }}>
          Loading delegated children…
        </p>
      )}
      {state.kind === "error" && (
        <p data-testid="synthesis-error" role="alert" style={{ color: "var(--red)", fontSize: 13 }}>
          Could not load the synthesis: {state.message}
        </p>
      )}
      {state.kind === "ready" && children.length === 0 && (
        <p data-testid="synthesis-empty" style={{ ...muted, fontSize: 13 }}>
          no delegated children
        </p>
      )}

      {children.length > 0 && (
        <table data-testid="synthesis-children"
          style={{ borderCollapse: "collapse", fontSize: 12, width: "100%" }}>
          <thead>
            <tr>
              {["Title", "Agent", "Delegated by", "Scope paths", "State",
                "Spent (USD)", "Run status", "Result"].map((h) => (
                <th key={h} style={cell}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {children.map((c) => (
              <tr key={c.assignmentId} data-testid={`synthesis-child-${c.assignmentId}`}>
                <td style={cell}>{c.title}</td>
                <td style={cell}>{c.agentName} v{c.agentVersion}</td>
                <td style={cell}>{c.delegatedBy}</td>
                <td style={cell}>{c.scope.paths.length > 0 ? c.scope.paths.join(", ") : "none"}</td>
                <td style={cell}>{c.state}</td>
                <td style={cell} data-testid={`synthesis-spend-${c.assignmentId}`}>{c.spentUsd}</td>
                <td style={cell}>{c.runStatus ?? "unknown"}</td>
                <td style={cell}>{c.result ?? "no result yet"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {view && view.conflicts.length > 0 && (
        <section data-testid="synthesis-conflicts" aria-label="Overlapping scopes"
          style={{ marginTop: 12 }}>
          <h3 style={{ fontSize: 13, margin: "0 0 4px" }}>Overlapping scopes</h3>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
            {view.conflicts.map((conflict, i) => (
              <li key={`${conflict.assignmentIds.join("-")}-${i}`}
                data-testid={`synthesis-conflict-${i}`}>
                {conflict.assignmentIds.map((id) => titles.get(id) ?? id).join(" and ")}
                {": "}{conflict.paths.join(", ")}
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}

/** Fetches the read-only synthesis. The panel is mounted only for a run that
 *  has (or had) delegated children, so an ordinary run gets no empty section:
 *  the first successful response with children is what reveals it. Once seen,
 *  the loading, error and empty states stay visible for that run. The 10 s
 *  interval is the agent-tree panel's own, never faster than an existing view. */
export function SynthesisSection({ runId, token }: { runId: string; token: string }) {
  const [state, setState] = useState<SynthesisState>({ kind: "loading" });
  const [seenChildren, setSeenChildren] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const load = () => {
      void getSynthesis(runId, token)
        .then((view) => {
          if (cancelled) return;
          setState({ kind: "ready", view });
          if (view.children.length > 0) setSeenChildren(true);
        })
        .catch((e: unknown) => {
          if (!cancelled) setState({ kind: "error", message: String((e as Error)?.message ?? e) });
        });
    };
    load();
    const id = setInterval(load, 10000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [runId, token]);
  const visible = seenChildren || (state.kind === "ready" && state.view.children.length > 0);
  if (!visible) return null;
  return <SynthesisPanel state={state} />;
}
