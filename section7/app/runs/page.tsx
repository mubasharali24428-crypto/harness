"use client";

// Mission 10 node A5 — /runs: run history across projects, client of
// contract M10-C2 (`GET /api/v1/orchestrator/runs`, served by Section 4's
// orchestrator). Filter bar (project/status/page size), server-driven paging
// via limit/offset/total, status pills, relative times with absolute titles.
// Real fetches against the orchestrator — no mocks.

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { listProjects } from "@/lib/api";
import { loadSession } from "@/lib/auth";
import {
  RUNS_DEFAULT_LIMIT,
  RUNS_MAX_LIMIT,
  RUN_STATUSES,
  RunDto,
  RunStatusValue,
  RunsPageDto,
  formatRelativeTime,
  listRuns,
  shortId,
} from "./runsApi";

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

/** Status → theme-var pill color. Unknown statuses fall through honestly. */
const statusPillClass: Record<string, string> = {
  queued: "pill blue",
  running: "pill yellow",
  completed: "pill green",
  failed: "pill red",
  paused: "pill yellow",
  blocked: "pill red",
};

const PAGE_SIZES = [10, 25, 50, 100] as const;

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
  // the API; the data itself only refetches on filter/paging changes.
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
        setError(String((e as Error)?.message ?? e));
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
        <div role="alert" className="pill red" style={{ padding: "8px 12px", marginBottom: 16 }}>
          {error}
        </div>
      )}

      <section className="card" style={cardStyle} aria-busy={loading}>
        <table style={tableStyle}>
          <caption className="sr-only" style={{ textAlign: "left" }}>
            Orchestrator runs, newest first
          </caption>
          <thead>
            <tr>
              <th style={thTdStyle}>Run</th>
              <th style={thTdStyle}>Project</th>
              <th style={thTdStyle}>Status</th>
              <th style={thTdStyle}>Created</th>
              <th style={thTdStyle}>Started</th>
              <th style={thTdStyle}>Finished</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id}>
                <td style={thTdStyle}>
                  <span title={r.id} style={{ fontFamily: "inherit" }}>
                    {shortId(r.id)}
                  </span>
                </td>
                <td style={thTdStyle}>{r.projectId}</td>
                <td style={thTdStyle}>
                  <span className={statusPillClass[r.status] ?? "pill"}>{r.status}</span>
                </td>
                <td style={thTdStyle} title={r.createdAt ?? undefined}>
                  {formatRelativeTime(r.createdAt, now)}
                </td>
                <td style={thTdStyle} title={r.startedAt ?? undefined}>
                  {formatRelativeTime(r.startedAt, now)}
                </td>
                <td style={thTdStyle} title={r.finishedAt ?? undefined}>
                  {formatRelativeTime(r.finishedAt, now)}
                </td>
              </tr>
            ))}
            {!loading && runs.length === 0 && !error && (
              <tr>
                <td style={thTdStyle} colSpan={6}>
                  No runs match the current filters.
                </td>
              </tr>
            )}
            {!loading && runs.length === 0 && error === null && total > 0 && (
              <tr>
                <td style={thTdStyle} colSpan={6}>
                  Server reported {total} runs but returned none at this offset.
                </td>
              </tr>
            )}
          </tbody>
        </table>

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
        Times are relative to your machine&apos;s clock ({RUNS_MAX_LIMIT} runs per page maximum).
      </p>
    </AppShell>
  );
}
