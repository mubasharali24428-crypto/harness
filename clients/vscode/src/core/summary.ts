/**
 * The review summary (W24): what the person approving a result needs beside the diff.
 *
 * The facts are built by `section4/.../review_summary.py` inside the confined runner and cross a
 * process boundary as JSON, so this module checks their shape before trusting them. It also
 * prices the tokens (the runner holds no price list) and renders the text the editor shows. Two
 * rules carry over from the builder: an unknown stays *unknown* on screen, and a cost that
 * cannot be priced is never shown as zero.
 */

export interface SummaryFile {
  path: string;
  change: string;
  added: number | null;
  removed: number | null;
}

export interface ReviewSummary {
  schema: number;
  basis: string;
  files: SummaryFile[];
  tests: { known: boolean; runs: Array<{ command: string; result: string }>; note?: string };
  failures: Array<{ kind: string; what: string }>;
  cost: {
    usd: number | null;
    source: string | null;
    model: string | null;
    inputTokens: number | null;
    outputTokens: number | null;
  };
  elapsedSeconds: number | null;
  uncertainty: Array<{ code: string; text: string }>;
}

/** The rate card's answer for one model (`POST /api/v1/models/estimate-cost`). */
export interface PriceEstimate {
  priced: boolean;
  usd: number;
}

const SCHEMA_VERSION = 1;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/** The summary, or `null` when the runner sent something this editor does not understand. */
export function parseSummary(raw: unknown): ReviewSummary | null {
  if (!isRecord(raw) || raw.schema !== SCHEMA_VERSION) return null;
  const { files, tests, failures, cost, uncertainty } = raw;
  if (!Array.isArray(files) || !isRecord(tests) || !Array.isArray(tests.runs)) return null;
  if (!Array.isArray(failures) || !isRecord(cost) || !Array.isArray(uncertainty)) return null;
  try {
    return {
      schema: SCHEMA_VERSION,
      basis: String(raw.basis ?? ""),
      files: files.map((f) => {
        if (!isRecord(f) || typeof f.path !== "string") throw new Error("file");
        return {
          path: f.path,
          change: String(f.change ?? "changed"),
          added: numberOrNull(f.added),
          removed: numberOrNull(f.removed),
        };
      }),
      tests: {
        known: tests.known === true,
        runs: tests.runs.map((r) => {
          if (!isRecord(r)) throw new Error("test");
          return { command: String(r.command ?? ""), result: String(r.result ?? "unknown") };
        }),
        ...(typeof tests.note === "string" ? { note: tests.note } : {}),
      },
      failures: failures.map((f) => {
        if (!isRecord(f)) throw new Error("failure");
        return { kind: String(f.kind ?? "run"), what: String(f.what ?? "") };
      }),
      cost: {
        usd: numberOrNull(cost.usd),
        source: stringOrNull(cost.source),
        model: stringOrNull(cost.model),
        inputTokens: numberOrNull(cost.inputTokens),
        outputTokens: numberOrNull(cost.outputTokens),
      },
      elapsedSeconds: numberOrNull(raw.elapsedSeconds),
      uncertainty: uncertainty.map((u) => {
        if (!isRecord(u)) throw new Error("uncertainty");
        return { code: String(u.code ?? ""), text: String(u.text ?? "") };
      }),
    };
  } catch {
    return null;
  }
}

/**
 * Fill the cost from the rate card. A model the card does not cover stays unpriced, with its
 * uncertainty entry intact: the answer for an unknown model is "unknown", not `$0.000000`.
 */
export function priceSummary(summary: ReviewSummary, estimate: PriceEstimate | null): ReviewSummary {
  if (!estimate || !estimate.priced || !Number.isFinite(estimate.usd)) return summary;
  return {
    ...summary,
    cost: { ...summary.cost, usd: estimate.usd, source: "rate card" },
    uncertainty: summary.uncertainty.filter((u) => u.code !== "COST_UNPRICED"),
  };
}

function lineCount(file: SummaryFile): string {
  return file.added === null || file.removed === null ? "" : `  +${file.added} −${file.removed}`;
}

function tokens(cost: ReviewSummary["cost"]): string {
  if (cost.inputTokens === null || cost.outputTokens === null) return "";
  return `${cost.inputTokens.toLocaleString("en-US")} in / ${cost.outputTokens.toLocaleString("en-US")} out`;
}

const MAX_FILES_SHOWN = 8;

/** The text of the confirm step: short lines, unknowns spelled out, nothing implied. */
export function renderSummary(summary: ReviewSummary): string {
  const out: string[] = [];
  const shown = summary.files.slice(0, MAX_FILES_SHOWN);
  out.push(summary.files.length ? `Changed files (${summary.files.length}):` : "Changed files: none");
  for (const file of shown) out.push(`  ${file.path}  ${file.change}${lineCount(file)}`);
  if (summary.files.length > shown.length) out.push(`  … and ${summary.files.length - shown.length} more`);

  if (!summary.tests.known) {
    out.push("Tests: unknown (the engine did not report its tool calls)");
  } else if (!summary.tests.runs.length) {
    out.push("Tests: none were run");
  } else {
    out.push("Tests:");
    for (const run of summary.tests.runs) out.push(`  ${run.command}  ${run.result}`);
    if (summary.tests.note) out.push(`  (${summary.tests.note})`);
  }

  if (!summary.failures.length) {
    out.push("Failures: none recorded");
  } else {
    out.push("Failures:");
    for (const failure of summary.failures) out.push(`  • ${failure.what}`);
  }

  const { cost } = summary;
  const counts = tokens(cost);
  const model = cost.model ? ` · ${cost.model}` : "";
  if (cost.usd === null) {
    out.push(`Cost: unknown${counts ? ` (${counts} tokens${model})` : ""}`);
  } else {
    out.push(`Cost: $${cost.usd.toFixed(6)}${counts ? ` · ${counts}` : ""}${model}` +
      `${cost.source ? ` · ${cost.source}` : ""}`);
  }
  out.push(summary.elapsedSeconds === null ? "Time: unknown" : `Time: ${summary.elapsedSeconds} s`);

  if (summary.uncertainty.length) {
    out.push("Still unknown:");
    for (const item of summary.uncertainty) out.push(`  • ${item.text}`);
  }
  if (summary.basis) out.push(`Basis: ${summary.basis}.`);
  return out.join("\n");
}
