import type { RunStatus } from "@contracts/api";
import type { ConnectionState } from "@/lib/useEventStream";
import type { WorkingAreaDerivedView } from "@/lib/deriveView";
import { panelStyle, headingStyle, pillStyle } from "@/lib/styles";

const STATE_COLORS: Record<string, string> = {
  idle: "#a8a296",
  planning: "#6cb8ff",
  awaiting_plan_approval: "#6cb8ff",
  executing: "#8ee5a1",
  paused: "#e5a814",
  awaiting_done_signal: "#6cb8ff",
  reviewing: "#c9a7ff",
  blocked: "#ff8c8c",
  pr_opened: "#6cd5c0",
  done: "#a8a296",
};

const CONNECTION_LABEL: Record<ConnectionState, string> = {
  connecting: "Connecting…",
  open: "Live",
  reconnecting: "Reconnecting…",
  closed: "Disconnected",
};

export function StatusPanel({
  status,
  derived,
  connectionState,
}: {
  status: RunStatus | null;
  derived: WorkingAreaDerivedView;
  connectionState: ConnectionState;
}) {
  const state = status?.state ?? "idle";
  return (
    <section style={panelStyle}>
      <h2 style={headingStyle}>Status</h2>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
        <span data-testid="run-state" style={pillStyle(STATE_COLORS[state] ?? "var(--muted)")}>{state.replaceAll("_", " ")}</span>
        <span style={{ fontSize: 12, color: connectionState === "open" ? "var(--accent)" : "var(--muted)" }}>
          ● {CONNECTION_LABEL[connectionState]}
        </span>
        {status?.isHumanEditing && <span style={pillStyle("var(--yellow)")}>human editing</span>}
      </div>

      {derived.blockerQuestion && (
        <div style={{ ...calloutStyle, borderColor: "var(--red)" }}>
          <strong>Blocked — awaiting clarification</strong>
          <p style={{ margin: "4px 0 0 0" }}>{derived.blockerQuestion}</p>
        </div>
      )}

      {derived.lastReview && (
        <div
          style={{
            ...calloutStyle,
            borderColor: derived.lastReview.passed ? "var(--accent)" : "var(--red)",
          }}
        >
          <strong>{derived.lastReview.passed ? "Reviewer passed" : "Reviewer failed"}</strong>
          <p style={{ margin: "4px 0 0 0" }}>{derived.lastReview.summary}</p>
          {derived.lastReview.issues.length > 0 && (
            <ul style={{ margin: "4px 0 0 0", paddingLeft: 18 }}>
              {derived.lastReview.issues.map((issue, i) => (
                <li key={i}>{issue}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {!derived.blockerQuestion && !derived.lastReview && (
        <p style={{ color: "var(--muted)", fontSize: 13 }}>No blockers. No review yet.</p>
      )}
    </section>
  );
}

const calloutStyle = {
  marginTop: 8,
  padding: 10,
  borderRadius: 6,
  border: "1px solid",
  fontSize: 13,
} as const;
