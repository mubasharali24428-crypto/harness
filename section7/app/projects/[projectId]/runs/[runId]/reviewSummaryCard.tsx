// W24: what a reviewer needs beside the diff, from GET runs/{runId}/review-summary. Every fact is
// recorded server-side; an unknown is written as unknown and a cost is never shown as $0.
import * as React from "react";

export interface ReviewSummaryView {
  basis: string;
  files: Array<{ path: string; change: string; added: number | null; removed: number | null }>;
  tests: { known: boolean; runs: Array<{ command: string; result: string }>; note?: string };
  failures: Array<{ what: string }>;
  cost: { usd: number | null; source: string | null; model: string | null; inputTokens: number | null; outputTokens: number | null };
  elapsedSeconds: number | null;
  uncertainty: Array<{ code: string; text: string }>;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const rows = (v: unknown): Record<string, any>[] => (Array.isArray(v) ? v.filter((x) => x && typeof x === "object") : []);

/** The summary inside the endpoint's `{runId, summary}` envelope, or null when it is not one we understand. */
export function readReviewSummary(raw: any): ReviewSummaryView | null {
  const s = raw?.summary;
  if (!s || s.schema !== 1 || !s.tests || !Array.isArray(s.tests.runs) || !s.cost) return null;
  return {
    basis: String(s.basis ?? ""),
    files: rows(s.files).map((f) => ({ path: String(f.path), change: String(f.change ?? "changed"), added: num(f.added), removed: num(f.removed) })),
    tests: { known: s.tests.known === true, runs: rows(s.tests.runs).map((r) => ({ command: String(r.command), result: String(r.result) })), note: typeof s.tests.note === "string" ? s.tests.note : undefined },
    failures: rows(s.failures).map((f) => ({ what: String(f.what) })),
    cost: { usd: num(s.cost.usd), source: s.cost.source ?? null, model: s.cost.model ?? null, inputTokens: num(s.cost.inputTokens), outputTokens: num(s.cost.outputTokens) },
    elapsedSeconds: num(s.elapsedSeconds),
    uncertainty: rows(s.uncertainty).map((u) => ({ code: String(u.code), text: String(u.text) })),
  };
}

export function ReviewSummaryCard({ summary: s }: { summary: ReviewSummaryView }) {
  const tokens = s.cost.inputTokens !== null && s.cost.outputTokens !== null
    ? `${s.cost.inputTokens.toLocaleString("en-US")} in / ${s.cost.outputTokens.toLocaleString("en-US")} out`
    : "";
  const model = s.cost.model ? ` · ${s.cost.model}` : "";
  return (
    <section aria-labelledby="review-summary-heading" style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 12, margin: "10px 0" }}>
      <h4 id="review-summary-heading" style={{ margin: "0 0 6px" }}>Review summary</h4>
      <dl style={{ margin: 0 }}>
        <dt>Changed files ({s.files.length})</dt>
        <dd>{s.files.length ? <ul>{s.files.map((f) => (
          <li key={f.path}>{f.path} · {f.change}{f.added === null || f.removed === null ? "" : ` · +${f.added} −${f.removed}`}</li>
        ))}</ul> : "none"}</dd>
        <dt>Tests</dt>
        <dd>{!s.tests.known ? "unknown (no tool calls were recorded for this run)"
          : !s.tests.runs.length ? "none were run"
          : <><ul>{s.tests.runs.map((r, i) => <li key={i}><code>{r.command}</code> · {r.result}</li>)}</ul>{s.tests.note ? <small>{s.tests.note}</small> : null}</>}</dd>
        <dt>Failures</dt>
        <dd>{s.failures.length ? <ul>{s.failures.map((f, i) => <li key={i}>{f.what}</li>)}</ul> : "none recorded"}</dd>
        <dt>Cost</dt>
        <dd>{s.cost.usd === null
          ? `unknown${tokens ? ` (${tokens} tokens${model})` : ""}`
          : `$${s.cost.usd.toFixed(6)}${tokens ? ` · ${tokens}` : ""}${model}${s.cost.source ? ` · ${s.cost.source}` : ""}`}</dd>
        <dt>Time</dt>
        <dd>{s.elapsedSeconds === null ? "unknown" : `${s.elapsedSeconds} s`}</dd>
      </dl>
      {s.uncertainty.length > 0 && <>
        <h5 style={{ margin: "10px 0 4px" }}>Still unknown</h5>
        <ul>{s.uncertainty.map((u) => <li key={u.code}>{u.text}</li>)}</ul>
      </>}
      {s.basis && <p><small>Basis: {s.basis}.</small></p>}
    </section>
  );
}
