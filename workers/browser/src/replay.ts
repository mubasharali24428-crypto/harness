import { sha256Hex } from "./digest.js";
import type { ActionOutcome, BrowserAction } from "./types.js";

/** One redacted, replayable record of an executed action. */
export interface ReplayEntry {
  index: number;
  action: Record<string, unknown>;
  status: ActionOutcome["status"];
  errorCode?: string;
  observationId?: string;
  domDigest?: string;
  url?: string;
  viewport?: { width: number; height: number; deviceScaleFactor: number };
  startedAtMs: number;
  durationMs: number;
  replayable: boolean;
}

/** Strip capability/approval material; replace typed text with its digest (not replayable without a provider). */
export function redactAction(action: BrowserAction): { action: Record<string, unknown>; replayable: boolean } {
  const { capabilityToken: _t, ...rest } = action as unknown as Record<string, unknown>;
  delete rest.approvalId;
  let replayable = true;
  if (rest.kind === "type" && typeof rest.text === "string") {
    rest.textDigest = sha256Hex(rest.text);
    rest.textLength = rest.text.length;
    delete rest.text;
    replayable = false;
  }
  return { action: rest, replayable };
}

export function buildEntry(
  index: number, action: BrowserAction, outcome: ActionOutcome, startedAtMs: number, durationMs: number,
): ReplayEntry {
  const { action: redacted, replayable } = redactAction(action);
  const o = outcome.observation;
  return {
    index, action: redacted, status: outcome.status, startedAtMs, durationMs, replayable,
    ...(outcome.error ? { errorCode: outcome.error.code } : {}),
    ...(o ? {
      observationId: o.observationId, domDigest: o.domDigest, url: o.url,
      viewport: { width: o.viewport.width, height: o.viewport.height, deviceScaleFactor: o.viewport.deviceScaleFactor },
    } : {}),
  };
}

export interface ReplayDivergence { index: number; kind: "status" | "domDigest" | "skipped"; expected?: string; actual?: string; note?: string }

/**
 * Re-run a recorded log in a fresh session. Typed text and approvals cannot be
 * replayed from a redacted log: those steps are reported as skipped, and an
 * approval-gated step is expected to stop at needsApproval.
 */
export async function replayLog(
  log: ReplayEntry[],
  run: (action: BrowserAction) => Promise<ActionOutcome>,
  opts: { textProvider?: (e: ReplayEntry) => string | undefined } = {},
): Promise<{ divergences: ReplayDivergence[]; replayed: number }> {
  const divergences: ReplayDivergence[] = [];
  let replayed = 0;
  for (const entry of log) {
    let action = entry.action as unknown as BrowserAction & { textDigest?: string };
    if (!entry.replayable) {
      const text = opts.textProvider?.(entry);
      if (text === undefined || sha256Hex(text) !== (entry.action as { textDigest?: string }).textDigest) {
        divergences.push({ index: entry.index, kind: "skipped", note: "typed text is redacted; supply a matching textProvider" });
        continue;
      }
      action = { ...action, text } as unknown as BrowserAction;
    }
    // observation ids from the original run do not exist here; refs must be re-resolved.
    const { observationId: _o, ...clean } = action as unknown as Record<string, unknown>;
    const outcome = await run(clean as unknown as BrowserAction);
    replayed += 1;
    if (outcome.status !== entry.status) {
      divergences.push({ index: entry.index, kind: "status", expected: entry.status, actual: outcome.status });
    } else if (entry.domDigest && outcome.observation && outcome.observation.domDigest !== entry.domDigest) {
      divergences.push({ index: entry.index, kind: "domDigest", expected: entry.domDigest, actual: outcome.observation.domDigest });
    }
  }
  return { divergences, replayed };
}
