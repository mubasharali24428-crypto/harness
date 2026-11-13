"use client";

// M14-F3b/F1b extraction of the runs table (repaired by the DS lane after a
// concurrent-write race left it without its imports — git history has the
// lane's original). Styles now come from the DS primitives' shared cell
// treatment (ui/Table) and status tones (tokens.statusTones) instead of
// page-local maps, per W4-VT5 "no page-local one-offs in migrated pages".
//
// Behavior contract unchanged: rows are click-through + keyboard (Enter/Space)
// targets to /projects/{projectId}/runs/{id}; Run/Project cells carry real
// <a> links for open-in-new-tab/copy-link; F2b's .table-scroll wrapper keeps
// wide viewports honest.

import type { RunDto } from "./runsApi";
import { runDetailHref, shortId } from "./runsApi";
import { formatRelativeTime, formatTimestamp } from "@/lib/format";
import { needsYouAction } from "@/lib/triage";
// Classic JSX transform (vitest/esbuild) needs React in scope for the .tsx
// extension (same convention as the other component files).
import * as React from "react";
import { Table, cellStyle, StatusPill } from "@/components/ui";
import { cssVar, fontSize } from "@/components/tokens";

export function isRowActivateKey(key: string): boolean {
  return key === "Enter" || key === " ";
}

export function RunsTable({ runs, now }: { runs: RunDto[]; now: number }) {
  return (
    // M14-F2b (raw-03 P2): overflow-x wrapper keeps wide tables inside their
    // card on ≤900px viewports (page never scrolls sideways); CSS pins the
    // first column while panning. See .table-scroll in app/globals.css.
    <Table caption="Orchestrator runs, newest first">
      <thead>
        <tr>
          <th style={cellStyle}>Run</th>
          <th style={cellStyle}>Project</th>
          <th style={cellStyle}>Status</th>
          <th style={cellStyle}>Created</th>
          <th style={cellStyle}>Started</th>
          <th style={cellStyle}>Finished</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => (
          <tr
            key={r.id}
            tabIndex={0}
            aria-label={`Open run ${shortId(r.id)}`}
            onClick={() => {
              window.location.assign(runDetailHref(r));
            }}
            onKeyDown={(e) => {
              if (isRowActivateKey(e.key)) {
                e.preventDefault();
                window.location.assign(runDetailHref(r));
              }
            }}
            style={{ cursor: "pointer", outlineOffset: -3 }}
          >
            <td style={cellStyle}>
              {/* Real links (not just row onClick) so open-in-new-tab and
                  copy-link keep working; href is centralized in runsApi.ts. */}
              <a
                href={runDetailHref(r)}
                title={r.id}
                style={{
                  color: "var(--accent)",
                  textDecoration: "underline",
                  fontFamily: "inherit",
                }}
                onClick={(e) => e.stopPropagation()}
              >
                {shortId(r.id)}
              </a>
            </td>
            <td style={cellStyle}>
              <a
                href={runDetailHref(r)}
                title={r.projectId}
                style={{ color: "var(--accent)", textDecoration: "underline" }}
                onClick={(e) => e.stopPropagation()}
              >
                {r.projectId}
              </a>
            </td>
            <td style={cellStyle}>
              {/* DS: semantic tone map over the real AgentState vocabulary;
                  unknown statuses degrade to the neutral blue pill. The
                  needs-you line names the action, so a row is readable
                  without opening it (U1 inbox rule). */}
              <StatusPill status={r.status}>{r.status}</StatusPill>
              {needsYouAction(r) ? (
                <span
                  style={{
                    display: "block",
                    marginTop: 4,
                    color: cssVar("muted"),
                    fontSize: fontSize.xs,
                  }}
                >
                  {needsYouAction(r)}
                </span>
              ) : null}
            </td>
            <td style={cellStyle} title={r.createdAt ? formatTimestamp(r.createdAt) : undefined}>
              {formatRelativeTime(r.createdAt, now)}
            </td>
            <td style={cellStyle} title={r.startedAt ? formatTimestamp(r.startedAt) : undefined}>
              {formatRelativeTime(r.startedAt, now)}
            </td>
            <td style={cellStyle} title={r.finishedAt ? formatTimestamp(r.finishedAt) : undefined}>
              {formatRelativeTime(r.finishedAt, now)}
            </td>
          </tr>
        ))}
        {runs.length === 0 && (
          <tr>
            <td style={cellStyle} colSpan={6}>
              No runs match the current filters.
            </td>
          </tr>
        )}
      </tbody>
    </Table>
  );
}
