import type { RunStatus } from "@contracts/api";
import type { ConnectionState } from "@/lib/useEventStream";
import type { WorkingAreaDerivedView } from "@/lib/deriveView";
import { panelStyle, headingStyle, pillStyle } from "@/lib/styles";

const STATE_COLORS: Record<string, string> = {
  idle: "#6b7280",
  planning: "#60a5fa",
  awaiting_plan_approval: "#60a5fa",
  executing: "#34d399",
  paused: "#fbbf24",
  awaiting_done_signal: "#60a5fa",
  reviewing: "#a78bfa",
  blocked: "#f87171",
  pr_opened: "#2dd4bf",
  done: "#9ca3af",
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
        <span data-testid="run-state" style={pillStyle(STATE_COLORS[state] ?? "#6b7280")}>{state.replaceAll("_", " ")}</span>
        <span style={{ fontSize: 12, color: connectionState === "open" ? "#34d399" : "#9aa4b2" }}>
          ● {CONNECTION_LABEL[connectionState]}
        </span>
        {status?.isHumanEditing && <span style={pillStyle("#fbbf24")}>human editing</span>}
      </div>

      {derived.blockerQuestion && (
        <div style={{ ...calloutStyle, borderColor: "#f87171" }}>
          <strong>Blocked — awaiting clarification</strong>
          <p style={{ margin: "4px 0 0 0" }}>{derived.blockerQuestion}</p>
        </div>
      )}

      {derived.lastReview && (
        <div
          style={{
            ...calloutStyle,
            borderColor: derived.lastReview.passed ? "#34d399" : "#f87171",
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
        <p style={{ color: "#6b7280", fontSize: 13 }}>No blockers. No review yet.</p>
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
