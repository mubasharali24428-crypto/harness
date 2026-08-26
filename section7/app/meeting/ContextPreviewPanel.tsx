"use client";

// M21-X3 — context-preview panel: what the agent WILL see for a brief's @refs.
//
// For each unique ref in the brief text the panel queries Section 11's frozen
// W21-C1 search API and renders the scored chunks (path:line-range + content
// snippet) exactly in retrieval order. This is the UI half of W21-C2: the
// preview shows the SAME query strings the S4 resolver will issue
// (makeRetrievalQuery) and the same lexical confirmation rule for file refs
// (pathConfirms), so "confirmed" here means what resolve_context promotes.
//
// Honesty law (repo-wide): every failure mode is visible — an unreachable
// index, an empty result, a ref with no confirmed path hit all render as
// explicit per-ref status lines, never fabricated content or silent blanks.

import { useEffect, useState } from "react";
import * as React from "react";
import { Pill } from "@/components/ui";
import {
  makeRetrievalQuery,
  parseRefs,
  pathConfirms,
  searchContext,
  type ScoredChunkDto,
} from "@/lib/contextApi";

export interface PreviewRow {
  refName: string;
  kind: "file" | "symbol";
  state: "loading" | "hits" | "empty" | "error" | "skipped";
  /** Chunks in wire order (score-ranked); present when state === "hits". */
  chunks: ScoredChunkDto[];
  error?: string;
}

/** Pure: which rows does this brief text need, preserving first-appearance? */
export function previewRowsForText(text: string): PreviewRow[] {
  const seen = new Set<string>();
  const rows: PreviewRow[] = [];
  for (const ref of parseRefs(text ?? "")) {
    if (seen.has(ref.name)) continue;
    seen.add(ref.name);
    rows.push({ refName: ref.name, kind: ref.kind, state: "loading", chunks: [] });
  }
  return rows;
}

/** Pure: one display snippet from chunk content — the first NON-EMPTY line
 *  (chunks often begin with blank lines), trimmed, hard-capped. */
export function chunkSnippet(content: string): string {
  for (const line of (content ?? "").split("\n")) {
    const t = line.trim();
    if (t) return t.length > 140 ? `${t.slice(0, 137)}…` : t;
  }
  return "";
}

/** Pure: score → pill class over the shared four-tone vocabulary.
 *  Confirmed-path hits are green regardless of raw score (the intent signal);
 *  positive scores blue; non-positive red — W21-C1 cosine scores are signed
 *  (verified live by X2), so ≤0 means anti-correlated noise worth flagging. */
export function scorePillClass(score: number, confirmed: boolean): string {
  if (confirmed) return "pill green";
  if (!Number.isFinite(score) || score <= 0) return "pill red";
  return "pill blue";
}

/** Pure: `path:startLine–endLine` location label for one chunk row. */
export function chunkLocation(chunk: ScoredChunkDto): string {
  return `${chunk.path}:${chunk.startLine}\u2013${chunk.endLine}`;
}

async function fetchRefChunks(
  row: PreviewRow,
  projectId: string,
  token: string,
): Promise<ScoredChunkDto[]> {
  const res = await searchContext(
    { query: makeRetrievalQuery(row.refName), projectId, topK: 4 },
    token,
  );
  return res.results;
}

export interface ContextPreviewPanelProps {
  /** Brief/requirements text whose @refs get resolved for display. */
  text: string;
  projectId: string;
  token: string;
}

/**
 * The stranger-facing proof that the context pillar exists: feed it brief
 * text with @refs, watch them resolve into real retrieved chunks.
 */
export function ContextPreviewPanel({ text, projectId, token }: ContextPreviewPanelProps) {
  const [rows, setRows] = useState<PreviewRow[]>(() => previewRowsForText(text));

  // Re-derive the row set whenever the text changes; keep already-fetched
  // results for refs that persist across edits (no refetch on unrelated keys).
  useEffect(() => {
    setRows((prev) => {
      const byName = new Map(prev.map((r) => [r.refName, r]));
      return previewRowsForText(text).map((r) => byName.get(r.refName) ?? r);
    });
  }, [text]);

  useEffect(() => {
    let cancelled = false;
    const pending = rows.filter((r) => r.state === "loading");
    if (pending.length === 0) return;

    void Promise.all(
      pending.map(async (row) => {
        try {
          const chunks = await fetchRefChunks(row, projectId, token);
          return { refName: row.refName, kind: row.kind, state: "hits" as const, chunks };
        } catch (err) {
          return {
            refName: row.refName,
            kind: row.kind,
            state: "error" as const,
            chunks: [],
            error: err instanceof Error ? err.message : String(err),
          };
        }
      }),
    ).then((updates) => {
      if (cancelled) return;
      setRows((prev) =>
        prev.map((row) => {
          const u = updates.find((x) => x.refName === row.refName);
          if (!u) return row;
          if (u.state === "hits") {
            return u.chunks.length === 0 ? { ...u, state: "empty" as const } : u;
          }
          return u as PreviewRow;
        }),
      );
    });

    return () => {
      cancelled = true;
    };
  }, [rows]); // eslint-disable-line react-hooks/exhaustive-deps -- rows IS the fetch trigger

  if (rows.length === 0) {
    return (
      <section className="card" data-testid="context-preview">
        <h2>Context preview</h2>
        <p style={{ fontSize: 13, opacity: 0.75 }}>
          Add @refs to the brief (@path/to/file.ext or @Symbol) to preview the exact
          chunks the agent will retrieve.
        </p>
      </section>
    );
  }

  return (
    <section className="card" data-testid="context-preview" aria-label="Context preview">
      <h2>Context preview</h2>
      <p style={{ fontSize: 12, opacity: 0.75 }}>
        What the agent WILL see — live retrieval against the project index.
      </p>
      {rows.map((row) => (
        <div key={row.refName} style={{ marginBottom: 12 }} data-testid={`preview-ref-${row.refName}`}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <strong style={{ fontSize: 13 }}>@{row.refName}</strong>
            {row.state === "loading" && <span style={{ opacity: 0.7, fontSize: 12 }}>resolving…</span>}
            {row.state === "empty" && (
              <span style={{ opacity: 0.7, fontSize: 12 }} role="status">No indexed matches.</span>
            )}
            {row.state === "error" && (
              <span style={{ color: "var(--danger, #c33)", fontSize: 12 }} role="alert">
                index unreachable: {row.error}
              </span>
            )}
          </div>
          {row.state === "hits" &&
            row.chunks.map((chunk, i) => {
              const confirmed = row.kind === "file" && pathConfirms(chunk.path, row.refName);
              return (
                <div
                  key={`${chunk.path}:${chunk.startLine}:${i}`}
                  style={{
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    padding: "6px 8px",
                    marginTop: 6,
                    background: "var(--panel-2)",
                  }}
                >
                  <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                    <code style={{ fontSize: 12 }}>{chunkLocation(chunk)}</code>
                    <span className={scorePillClass(chunk.score, confirmed)} title={`score ${chunk.score}`}>
                      {Number.isFinite(chunk.score) ? chunk.score.toFixed(3) : "?"}
                      {confirmed ? " · confirmed" : ""}
                    </span>
                  </div>
                  <pre
                    style={{
                      margin: "4px 0 0",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      fontSize: 12,
                      fontFamily: "ui-monospace, monospace",
                    }}
                  >
                    {chunkSnippet(chunk.content)}
                  </pre>
                </div>
              );
            })}
        </div>
      ))}
    </section>
  );
}
