import * as React from "react";
import type { RunStatus } from "@contracts/api";
import type { ConnectionState } from "@/lib/useEventStream";
import type { WorkingAreaDerivedView } from "@/lib/deriveView";
import { panelStyle, headingStyle, pillStyle } from "@/lib/styles";

// Theme tokens, never literals: these pills render on both paper and charcoal.
const STATE_COLORS: Record<string, string> = {
  idle: "var(--muted)",
  planning: "var(--blue)",
  awaiting_plan_approval: "var(--blue)",
  executing: "var(--accent)",
  paused: "var(--yellow)",
  awaiting_done_signal: "var(--blue)",
  reviewing: "var(--violet)",
  blocked: "var(--red)",
  pr_opened: "var(--teal)",
  done: "var(--muted)",
};

const CONNECTION_LABEL: Record<ConnectionState, string> = {
  connecting: "Connecting…",
  open: "Live",
  reconnecting: "Reconnecting…",
  closed: "Disconnected",
};

function formatWindow(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(tokens % 1_000_000 === 0 ? 0 : 1)}M`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}k`;
  return String(tokens);
}

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

      {(derived.contextWindow !== null || derived.compactedTokens > 0) && (
        <p style={{ margin: "0 0 10px 0", fontSize: 12, color: "var(--muted)" }}>
          Context:{" "}
          {derived.contextWindow !== null ? `${formatWindow(derived.contextWindow)} window` : "window unknown"}
          {derived.compactedTokens > 0
            ? ` · ${derived.compactedTokens.toLocaleString()} tokens compacted`
            : " · no compaction yet"}
        </p>
      )}

      {derived.goalImages && (
        <p data-testid="goal-images" style={{ margin: "0 0 10px 0", fontSize: 12, color: "var(--muted)" }}>
          {derived.goalImages.attached > 0
            ? `${derived.goalImages.attached} image${derived.goalImages.attached === 1 ? "" : "s"} with the goal`
            : "no images reached the model"}
          {derived.goalImages.skipped.length > 0 &&
            ` · ${derived.goalImages.skipped.length} not sent (${derived.goalImages.skipped
              .map((s) => `${s.name}: ${s.reason}`)
              .join("; ")})`}
          {derived.goalImages.refused && ` · ${derived.goalImages.refused}`}
        </p>
      )}

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
