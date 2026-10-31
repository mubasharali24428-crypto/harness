"use client";

// M14-W4 gate repair — presentational run-detail aggregate view extracted
// from app/projects/[projectId]/runs/[runId]/page.tsx (F3b/W4-C2) so the page
// module keeps ONLY Next's allowed route exports. Named export lets component
// tests prove render-per-field-presence without routing or sessions.
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too, so one source fits both.
import * as React from "react";
import { formatTimestamp } from "@/lib/format";
import {
  type RunAggregateDto,
  formatCostUsdExact,
  statePillClass,
  stateLabel,
  stepLabel,
  stepsNewestFirst,
} from "../../../../runs/runsApi";
import { type ConnStatus, type UserStatus, USER_STATUS_TEXT, liveStatus } from "./realtimeCore";

const USER_STATUS_PILL: Record<UserStatus, string> = {
  working: "pill yellow",
  waiting: "pill blue",
  reconnecting: "pill",
  blocked: "pill red",
  finished: "pill green",
};

const sectionStyle = { maxWidth: 980, marginBottom: 20 } as const;
const mutedStyle = { color: "var(--muted)" } as const;

/** Presentational aggregate view for one run — named export so component
 *  tests can drive render-per-field-presence without routing or sessions. */
export function RunDetailAggregate({
  agg,
  now,
  conn = "live",
}: {
  agg: RunAggregateDto;
  now: number;
  /** How fresh `agg` is; the page passes `reconnecting` after a failed refresh. */
  conn?: ConnStatus;
}) {
  const timeline = stepsNewestFirst(agg.steps);
  const status = liveStatus(agg.lifecycle?.userStatus, conn);
  return (
    <section className="card" style={sectionStyle} aria-label="Run summary">
      <h1 style={{ marginTop: 0 }}>
        Run <span title={agg.runId}>{agg.runId}</span>{" "}
        <span className={statePillClass(agg.state)} title={agg.state}>{stateLabel(agg.state)}</span>
      </h1>

      {/* W23: one of five user states. An older server sends no userStatus: show nothing. */}
      {status && (
        <p style={{ margin: "4px 0", fontSize: 14 }}>
          <strong>Status:</strong>{" "}
          <span data-testid="run-user-status" role="status" className={USER_STATUS_PILL[status]}>
            {USER_STATUS_TEXT[status]}
          </span>
        </p>
      )}

      {/* Cost line ONLY when the server sent a real number. Null means "no
          ledger rows yet" and must stay invisible — never a fake $0. A
          server-sent 0 IS real data and renders as $0.000000. */}
      {typeof agg.costUsd === "number" && Number.isFinite(agg.costUsd) && (
        <p style={{ margin: "4px 0", fontSize: 14 }}>
          <strong>Cost:</strong>{" "}
          <span data-testid="run-cost">{formatCostUsdExact(agg.costUsd)}</span>
        </p>
      )}

      {/* M18-C2: budget-gate health, surfaced ONLY when degraded — an
          operator-visible marker that a budget decision on this run was made
          fail-closed because the money store was unreachable. Healthy runs
          render nothing (no noise for the normal case). */}
      {agg.budget?.degraded === true && (
        <p
          data-testid="budget-degraded"
          role="status"
          className="pill red"
          style={{ margin: "4px 0", fontSize: 13 }}
        >
          Budget gate degraded: a spend decision ran fail-closed (budget store
          unreachable).
        </p>
      )}

      {/* PR card renders when the run is bound to a PR. prId alone still gets
          an identity line; url upgrades the title into a real link. url can
          legitimately arrive without prId (drift-tolerant) — still rendered. */}
      {(agg.prId !== null || agg.url !== null) && (
        <section
          className="card"
          style={{ marginBottom: 12 }}
          aria-label="Pull request"
        >
          <h2 style={{ margin: "0 0 8px 0", fontSize: 14 }}>Pull request</h2>
          <p style={{ margin: 0, fontSize: 13 }}>
            {agg.url ? (
              <a
                href={agg.url}
                target="_blank"
                rel="noreferrer"
                style={{ color: "var(--accent)", textDecoration: "underline" }}
              >
                View pull request →
              </a>
            ) : (
              <span style={mutedStyle}>No URL yet</span>
            )}
          </p>
          {agg.prId && (
            <p style={{ ...mutedStyle, margin: "6px 0 0", fontSize: 12 }}>
              prId: <code>{agg.prId}</code>
            </p>
          )}
        </section>
      )}

      {/* Step timeline: last ≤100 events, NEWEST FIRST. The count prefix is
          honest about truncation ("latest N of M"). An empty list means the
          server sent no events — rendered as an explicit empty state, never
          as invented filler rows. Timestamps degrade to "—" via the shared
          formatter when ts is null. */}
      <section aria-label="Step timeline">
        <h2 style={{ margin: "16px 0 8px 0", fontSize: 14 }}>
          Steps{" "}
          <span style={mutedStyle}>
            ({timeline.length} latest of {agg.steps.length})
          </span>
        </h2>
        {timeline.length === 0 ? (
          <p style={{ ...mutedStyle, fontSize: 13 }}>
            No events recorded for this run yet.
          </p>
        ) : (
          <ol
            style={{
              margin: 0,
              paddingLeft: 20,
              display: "grid",
              gap: 6,
              fontSize: 13,
            }}
          >
            {timeline.map((s) => (
              <li key={`${s.seq}-${s.type}`} title={formatTimestamp(s.ts)}>
                <code>{stepLabel(s.type, s.reason)}</code>{" "}
                <span style={mutedStyle}>
                  · seq {Number.isFinite(s.seq) ? s.seq : "?"} ·{" "}
                  {formatTimestamp(s.ts)}
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>

      {/* now is consumed by the shared time formatters' contract; keeping it
          in props keeps this component pure and SSR/test-renderable. */}
      <span hidden data-testid="now-ms">{String(now)}</span>
    </section>
  );
}
