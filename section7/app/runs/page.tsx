"use client";

// M12-E3b (H-009) — /runs: run history across projects, client of contract
// M10-C2 (`GET /api/v1/orchestrator/runs`, served by Section 4's orchestrator).
// Filter bar (project/status/page size), server-driven paging via
// limit/offset/total, status pills, relative times with absolute titles.
// Real fetches against the orchestrator — no mocks.
//
// H-009: every row is now a click-through to the run detail route
// /projects/{projectId}/runs/{id} (data already on the row): id + goal cells
// wrap a real next/link, the whole <tr> is clickable with keyboard support
// (tabIndex 0 + Enter/Space), and accent color makes it read as a link.

import { useCallback, useEffect, useMemo, useState } from "react";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension; Next's compiler accepts this import too, so one source fits both.
import * as React from "react";
import AppShell from "@/components/AppShell";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { listProjects } from "@/lib/api";
import { loadSession } from "@/lib/auth";
import { loadErrorMessage } from "@/lib/humanize";
import { isRowActivateKey, RunsTable } from "./runsTable";
import {
  RUNS_DEFAULT_LIMIT,
  RUNS_MAX_LIMIT,
  RUN_STATUSES,
  RunDto,
  RunStatusValue,
  RunsPageDto,
  listRuns,
  runDetailHref,
  shortId,
} from "./runsApi";
// M13-G5b: times render through the ONE shared client formatter
// (src/lib/format.ts) — same vocabulary as settings/checkpoints — instead of a
// local helper. Relative label as the cell text; absolute localized timestamp
// as the hover title.
import { formatRelativeTime, formatTimestamp } from "@/lib/format";

const cardStyle = { maxWidth: 980, marginBottom: 20 } as const;
const tableStyle = { width: "100%", borderCollapse: "collapse", fontSize: 13 } as const;
const thTdStyle = {
  textAlign: "left",
  padding: "6px 8px",
  borderBottom: "1px solid var(--border)",
} as const;

const selectStyle = {
  background: "var(--panel-2)",
  border: "1px solid var(--border)",
  color: "var(--text)",
  borderRadius: 6,
  padding: "6px 8px",
} as const;

/** Status → theme-var pill color over the REAL AgentState vocabulary
 *  (section4 state_machine.py). Active work = yellow, human/waiting gates =
 *  blue, terminal success = green, failure = red; unknown values fall through
 *  honestly to the uncolored .pill base. */
const statusPillClass: Record<string, string> = {
  idle: "pill blue",
  planning: "pill yellow",
  awaiting_plan_approval: "pill blue",
  executing: "pill yellow",
  paused: "pill yellow",
  awaiting_done_signal: "pill blue",
  reviewing: "pill yellow",
  blocked: "pill red",
  pr_opened: "pill blue",
  done: "pill green",
};

const PAGE_SIZES = [10, 25, 50, 100] as const;

/** M14-F1b (raw-03 P2): the ONE predicate deciding which keys activate a run
 *  row. Extracted so the W3 keyboard contract (Enter AND Space navigate) is
 *  unit-testable without a DOM; the row's onKeyDown consumes it below. */
/** Presentational runs table (H-009 click-through). Extracted as a named
 *  export so component tests can drive it without Next routing or a live
 *  session. Rows navigate to /projects/{projectId}/runs/{runId}: the Run +
 *  Project cells carry real <a> links for middle-click/copy-link, and the
 *  full <tr> is a click/keyboard (Enter/Space) target too. */
export default function RunsPage() {
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [projectId, setProjectId] = useState<string>("");
  const [status, setStatus] = useState<RunStatusValue | "">("");
  const [pageSize, setPageSize] = useState<number>(RUNS_DEFAULT_LIMIT);
  const [offset, setOffset] = useState<number>(0);

  const [page, setPage] = useState<RunsPageDto | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Ticking clock so relative times ("4m ago") stay honest without polling
  // the API; the data itself only refetches on filter/paging changes. The
  // clock value is passed into the table so rows re-render honestly.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(t);
  }, []);

  const fetchPage = useCallback(
    async (opts: { projectId: string; status: RunStatusValue | ""; limit: number; offset: number }) => {
      const session = loadSession();
      if (!session) {
        setError("Not signed in — log in first.");
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const data = await listRuns(
          {
            projectId: opts.projectId || null,
            status: opts.status || null,
            limit: opts.limit,
            offset: opts.offset,
          },
          session.token,
        );
        setPage(data);
      } catch (e) {
        setPage(null);
        setError(loadErrorMessage(e));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  // Initial load + project options for the filter select.
  useEffect(() => {
    const session = loadSession();
    if (!session) {
      setError("Not signed in — log in first.");
      return;
    }
    listProjects(session.token)
      .then((items) => setProjects(items))
      .catch(() => setProjects([])); // filter bar degrades to free-text id
    fetchPage({ projectId: "", status: "", limit: RUNS_DEFAULT_LIMIT, offset: 0 });
  }, [fetchPage]);

  const applyFilters = useCallback(
    (next: Partial<{ projectId: string; status: RunStatusValue | ""; pageSize: number }>) => {
      const nextProjectId = next.projectId ?? projectId;
      const nextStatus = next.status ?? status;
      const nextPageSize = next.pageSize ?? pageSize;
      if (next.projectId !== undefined) setProjectId(next.projectId);
      if (next.status !== undefined) setStatus(next.status);
      if (next.pageSize !== undefined) setPageSize(next.pageSize);
      setOffset(0); // any filter/size change resets paging
      void fetchPage({
        projectId: nextProjectId,
        status: nextStatus,
        limit: nextPageSize,
        offset: 0,
      });
    },
    [projectId, status, pageSize, fetchPage],
  );

  const goOffset = useCallback(
    (nextOffset: number) => {
      const clamped = Math.max(0, nextOffset);
      setOffset(clamped);
      void fetchPage({ projectId, status, limit: pageSize, offset: clamped });
    },
    [projectId, status, pageSize, fetchPage],
  );

  const runs: RunDto[] = useMemo(() => page?.items ?? [], [page]);
  const total = page?.total ?? 0;
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + runs.length, total);

  return (
    <AppShell active="/runs">
      <h1>Runs</h1>

      <section className="card" style={cardStyle}>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <div>
            <label htmlFor="runs-project" style={{ display: "block", color: "var(--muted)", marginBottom: 4 }}>
              Project
            </label>
            <select
              id="runs-project"
              value={projectId}
              onChange={(e) => applyFilters({ projectId: e.target.value })}
              style={{ ...selectStyle, minWidth: 220 }}
            >
              <option value="">All projects</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
              {/* Free-text fallback so a known id is still selectable even when
                  the project list failed to load or the run predates it. */}
              {projectId && !projects.some((p) => p.id === projectId) && (
                <option value={projectId}>{projectId}</option>
              )}
            </select>
          </div>

          <div>
            <label htmlFor="runs-status" style={{ display: "block", color: "var(--muted)", marginBottom: 4 }}>
              Status
            </label>
            <select
              id="runs-status"
              value={status}
              onChange={(e) => applyFilters({ status: e.target.value as RunStatusValue | "" })}
              style={selectStyle}
            >
              <option value="">All statuses</option>
              {RUN_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="runs-page-size" style={{ display: "block", color: "var(--muted)", marginBottom: 4 }}>
              Page size
            </label>
            <select
              id="runs-page-size"
              value={pageSize}
              onChange={(e) => applyFilters({ pageSize: Number(e.target.value) })}
              style={selectStyle}
            >
              {PAGE_SIZES.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>

          <button
            type="button"
            className="btn"
            onClick={() =>
              fetchPage({ projectId, status, limit: pageSize, offset })
            }
            disabled={loading}
          >
            Refresh
          </button>
        </div>
      </section>

      {error && (
        <ErrorState
          title="Runs didn’t load"
          message={error}
          onRetry={() => fetchPage({ projectId, status, limit: pageSize, offset })}
        />
      )}

      {loading && !page && !error && <LoadingState label="Loading runs…" rows={5} />}

      {!loading && !error && page !== null && runs.length === 0 && total === 0 && (
        <EmptyState
          title="No runs yet"
          body="Start from the Command Center: describe a goal, approve the plan, and the run shows up here with its cost and state."
          action={
            <a className="btn" href="/">
              Open the Command Center
            </a>
          }
        />
      )}

      <section className="card" style={cardStyle} aria-busy={loading}>
        <RunsTable runs={runs} now={now} />
        {!loading && runs.length === 0 && error === null && total > 0 && (
          <p style={{ color: "var(--muted)" }}>
            Server reported {total} runs but returned none at this offset.
          </p>
        )}

        <div
          style={{
            display: "flex",
            gap: 12,
            alignItems: "center",
            marginTop: 12,
            flexWrap: "wrap",
          }}
        >
          <span style={{ color: "var(--muted)" }} aria-live="polite">
            Showing {rangeStart}–{rangeEnd} of {total}
          </span>
          <button
            type="button"
            className="btn"
            onClick={() => goOffset(offset - pageSize)}
            disabled={loading || offset <= 0}
          >
            ← Prev
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => goOffset(offset + pageSize)}
            disabled={loading || offset + pageSize >= total}
          >
            Next →
          </button>
          {(projectId || status) && (
            <button
              type="button"
              className="btn"
              onClick={() => applyFilters({ projectId: "", status: "" })}
              disabled={loading}
            >
              Clear filters
            </button>
          )}
        </div>
      </section>

      <p style={{ color: "var(--muted)", fontSize: 12 }}>
        Times are relative to your machine&apos;s clock ({RUNS_MAX_LIMIT} runs per page maximum). Rows link to
        the run detail view.
      </p>
    </AppShell>
  );
}
