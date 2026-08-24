"use client";

// M12-E7 (raw-04 M1/M2) — presentational pieces of the onboarding checklist.
// Extracted from app/onboarding/page.tsx as named exports so vitest can drive
// them with react-dom/server's real renderer in the node environment (no jsdom
// here), same protocol as planGate.tsx / RunsTable.
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too — same as planGate.tsx.
import * as React from "react";
import type { ChecklistItemDto } from "./onboardingApi";
import { type SignalState, stepActionHref, stepActionLabel } from "./onboardingDerived";

const rowStyle = {
  display: "flex",
  alignItems: "flex-start",
  gap: 10,
  padding: "10px 0",
  borderBottom: "1px solid var(--border)",
} as const;

const linkStyle = {
  color: "var(--accent)",
  textDecoration: "underline",
  fontSize: 13,
  marginLeft: 10,
} as const;

/**
 * The checklist rows (raw-04 M1): every not-done step whose satisfaction lives
 * on another surface carries a deep link to that surface, so the checklist
 * enables work instead of merely describing it. Done steps render exactly as
 * before (no action link — nothing left to do).
 */
export function ChecklistRows({ items }: { items: ChecklistItemDto[] }) {
  return (
    <ol style={{ listStyle: "none", margin: 0, padding: 0 }} data-testid="checklist-items">
      {items.map((item, index) => {
        const href = stepActionHref(item.id);
        const label = stepActionLabel(item.id);
        return (
          <li
            key={item.id}
            style={rowStyle}
            data-testid={`checklist-item-${item.id}`}
          >
            <span aria-hidden="true" style={{ fontSize: 18, lineHeight: "22px" }}>
              {item.done ? "✅" : "⬜"}
            </span>
            <div style={{ flex: 1 }}>
              <div>
                <strong>{`${index + 1}. ${item.title}`}</strong>{" "}
                <span
                  className={item.done ? "pill green" : "pill blue"}
                  style={{ marginLeft: 6 }}
                >
                  {item.done ? "Done" : "To do"}
                </span>
                {!item.done && href && label && (
                  <a
                    href={href}
                    style={linkStyle}
                    title={`${label} — the surface where this step happens`}
                  >
                    {label} →
                  </a>
                )}
              </div>
              {item.description && (
                <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 2 }}>
                  {item.description}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** One live-signal readout line: ✓ / ⬜ / — (unknown), never faked. */
function SignalLine({
  done,
  value,
  children,
}: {
  done: boolean | null;
  value: string;
  children: React.ReactNode;
}) {
  return (
    <li style={{ fontSize: 13, lineHeight: 1.9, color: "var(--muted)" }}>
      <span aria-hidden="true">{done === null ? "—" : done ? "✅" : "⬜"}</span>{" "}
      {children}: <strong style={{ color: "var(--text)" }}>{value}</strong>
    </li>
  );
}

/**
 * Live signals card (raw-04 M1): shows the real-world conditions the
 * auto-complete derivation reads, with their current values. Unknown (failed/
 * unfetched probe) renders as an honest dash — never as progress.
 */
export function SignalsCard({ signals }: { signals: SignalState }) {
  return (
    <section className="card" style={{ maxWidth: 760, marginBottom: 20 }}>
      <h2>Live signals</h2>
      <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 0 }}>
        These conditions are read straight from the running services whenever you focus this
        page — when one becomes true, the matching step above completes itself. A dash means
        the signal could not be read right now; it never counts as progress.
      </p>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        <SignalLine done={signals.githubConnected} value={signals.githubConnected === null ? "unknown" : signals.githubConnected ? "connected" : "not connected"}>
          GitHub connection
        </SignalLine>
        <SignalLine
          done={signals.teamExists === null ? null : signals.teamExists && (signals.membersCount ?? 0) > 1}
          value={signals.membersCount === null ? "unknown" : `${signals.membersCount} member${signals.membersCount === 1 ? "" : "s"}`}
        >
          Team &amp; members (auto-completes “Create a team” and, at more than one member,
          “Invite members”)
        </SignalLine>
        <SignalLine
          done={signals.keysCount === null ? null : signals.keysCount > 0}
          value={signals.keysCount === null ? "unknown" : String(signals.keysCount)}
        >
          Provider API keys on file
        </SignalLine>
        <SignalLine
          done={signals.runsTotal === null ? null : signals.runsTotal > 0}
          value={signals.runsTotal === null ? "unknown" : String(signals.runsTotal)}
        >
          Orchestrator runs recorded
        </SignalLine>
      </ul>
    </section>
  );
}
