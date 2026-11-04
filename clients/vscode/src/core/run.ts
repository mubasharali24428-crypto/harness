/**
 * One request, from the editor to reviewed bytes (W7 slice 3).
 *
 * This module is the whole policy round-trip and imports nothing from
 * `vscode`: the transport is the SDK, the editor is an injected port, and the
 * tests drive both with fakes. What it enforces, in order:
 *
 * 1. the goal is the rendered context plan, so the audit's goal *is* what the
 *    editor showed;
 * 2. a plan is approved or rejected through the server gate — the editor never
 *    starts an unapproved run;
 * 3. execution is the server's confined run; this code has no local executor;
 * 4. the diff shown is the server patch whose digest the review will name;
 * 5. only approved changes are written, and only to the bytes that were shown
 *    (a file that moved since is refused, not overwritten).
 */

import type { ContextItem, ContextPlan } from "./context.js";
import { ContextError, planContext, renderGoal } from "./context.js";
import { parseSummary, priceSummary, type PriceEstimate, type ReviewSummary } from "./summary.js";
import {
  applyParsedPatch,
  parseFilePatch,
  PatchError,
  planApply,
  splitPatchByFile,
  type ApplyPlan,
  type RunDiffItem,
} from "./diff.js";

export class RunError extends Error {}

export interface PlanOffer {
  planId: string;
  /** Human-readable plan text shown before approval. */
  text: string;
}

export interface ChangeView {
  id: string;
  path: string;
  changeKind: string;
  diffSha: string;
  reviewState: "pending" | "approved" | "rejected" | string;
}

export interface HarnessTransport {
  proposePlan(goal: string): Promise<PlanOffer>;
  /** Approval is the gate: the server returns the run id it authorised. */
  approvePlan(planId: string): Promise<{ runId: string }>;
  rejectPlan(planId: string): Promise<void>;
  /** The operator's done-signal; it is what records the diff server-side. */
  confirmDone(runId: string, diffText: string): Promise<void>;
  diff(runId: string): Promise<RunDiffItem[]>;
  changes(runId: string): Promise<ChangeView[]>;
  reviewChange(
    runId: string,
    changeId: string,
    decision: "approved" | "rejected",
    diffSha: string,
    note?: string,
  ): Promise<void>;
  /**
   * The rate card's price for a token count, so a local run is priced the way a queued one is.
   * Optional: without it the cost stays unknown, never zero.
   */
  priceTokens?(model: string, inputTokens: number, outputTokens: number): Promise<PriceEstimate | null>;
}

export interface EditorPort {
  /** The operator's decision on the plan; the editor asks, this module obeys. */
  showPlan(offer: PlanOffer): Promise<"approve" | "reject">;
  /** Progress for the status bar and the output channel. */
  report(state: string, detail?: string): void;
  /**
   * Render the inline diff for one file (null = file absent on that side).
   * `stage` says which decision this diff is serving: `proposed` is the
   * engine's result awaiting a done-signal, `reviewed` is the recorded change
   * awaiting approve/reject. They are different promises, so they are labelled
   * differently.
   */
  showDiff(
    path: string,
    before: string | null,
    after: string | null,
    stage: "proposed" | "reviewed",
  ): Promise<void>;
  /** Show a patch this editor cannot apply locally (drifted base, binary, …). */
  showPatch(path: string, patch: string): Promise<void>;
  /**
   * The operator's done-signal decision, after seeing the proposed result and the review summary
   * (null when the runner sent none this editor understands).
   */
  confirmResult(summary: {
    files: string[];
    chars: number;
    review: ReviewSummary | null;
  }): Promise<boolean>;
  /** The operator's decision on one file, after seeing its diff. */
  decide(change: ChangeView, position: { index: number; total: number }): Promise<"approve" | "reject">;
  readFile(path: string): Promise<string | null>;
  writeFile(path: string, content: string): Promise<void>;
  deleteFile(path: string): Promise<void>;
}

export interface ExecutionResult {
  /** `completed` is the only value that lets the review begin. */
  state: string;
  summary: string;
  /** The patch the confined run produced, as unified-diff text. */
  diff: string;
  diffOmitted?: string | null;
  /** Named in the evidence: an IDE run must say what bounded it. */
  confinement?: string;
  image?: string;
  detail?: string;
  /** W24: the runner's review summary, as sent; parsed and priced before it is shown. */
  reviewSummary?: unknown;
}

/**
 * Where an approved plan executes. The policy gate is already satisfied by the
 * time this is called, and it cannot be skipped: an implementation that runs
 * agent code outside the container boundary is not an `Execution`, it is the
 * bypass this week's gate exists to refuse.
 */
export interface Execution {
  run(input: {
    runId: string;
    planId: string;
    goal: string;
    report: (state: string, detail?: string) => void;
  }): Promise<ExecutionResult>;
  /**
   * The run's own copy of a changed file, when it kept one. Used only as a
   * cross-check: if the editor's applier and the run disagree about a file's
   * bytes, neither is written.
   */
  readOutput?(path: string): Promise<string | null>;
}

export interface RunOptions {
  task: string;
  context?: ContextItem[];
  cap?: number;
  execution: Execution;
}

export interface Refusal {
  path: string;
  reason: string;
}

export interface RunOutcome {
  runId: string;
  goal: string;
  state: string;
  planned: boolean;
  confirmed: boolean;
  /** Named so evidence can say what bounded the run, not just that it ran. */
  confinement: string | null;
  image: string | null;
  /** W24: what the operator was shown beside the diff; null when there was none to show. */
  reviewSummary: ReviewSummary | null;
  reviewed: Array<{ path: string; decision: "approved" | "rejected"; diffSha: string }>;
  applied: string[];
  deleted: string[];
  refused: Refusal[];
}

/**
 * Run one request. Throws `RunError` when the gate itself fails (the plan was
 * rejected, the run failed, the deadline expired); per-file problems are
 * collected in `refused` so one bad file cannot hide the others.
 */
export async function runFromEditor(
  transport: HarnessTransport,
  editor: EditorPort,
  options: RunOptions,
): Promise<RunOutcome> {
  const plan: ContextPlan = planContext(options.context ?? [], options.cap);
  const goal = renderGoal(options.task, plan);
  editor.report("planning", `${goal.length} chars, ${plan.items.length} context items`);

  const offer = await transport.proposePlan(goal);
  const decision = await editor.showPlan(offer);
  if (decision === "reject") {
    await transport.rejectPlan(offer.planId);
    throw new RunError(`plan ${offer.planId} was rejected; nothing was executed`);
  }
  const { runId } = await transport.approvePlan(offer.planId);

  // Execution is injected, and it is the only place work happens. The IDE does
  // not get a second executor: this call is the W6 local-engine path, i.e. the
  // same confine module, the same container boundary and the same runner script
  // the CLI uses, launched with the plan the server just authorised.
  const executed = await options.execution.run({
    runId,
    planId: offer.planId,
    goal,
    report: (state, detail) => editor.report(state, detail),
  });
  const outcome: RunOutcome = {
    runId,
    goal,
    state: executed.state,
    planned: true,
    confirmed: false,
    confinement: executed.confinement ?? null,
    image: executed.image ?? null,
    reviewSummary: null,
    reviewed: [],
    applied: [],
    deleted: [],
    refused: [],
  };
  if (executed.state !== "completed") {
    throw new RunError(
      `the confined run ended ${executed.state}: ${executed.detail || executed.summary || "(no reason given)"}`,
    );
  }

  // What the operator sees beside the diff. The runner cannot price tokens, so the cost is
  // filled from the rate card here; a model it does not cover stays unpriced.
  let review = parseSummary(executed.reviewSummary);
  if (review && review.cost.usd === null && review.cost.model && transport.priceTokens
      && review.cost.inputTokens !== null && review.cost.outputTokens !== null) {
    const estimate = await transport
      .priceTokens(review.cost.model, review.cost.inputTokens, review.cost.outputTokens)
      .catch(() => null);
    review = priceSummary(review, estimate);
  }
  outcome.reviewSummary = review;

  // The engine finishing is not the run finishing: the product records a diff
  // only when a human confirms it (`done-signal`). The editor shows the patch
  // the run produced and asks; declining leaves nothing recorded.
  {
    const files = splitPatchByFile(executed.diff);
    if (!executed.diff.trim() || !files.length) {
      editor.report("no changes", executed.diffOmitted ?? "the run touched no file");
      outcome.refused.push({
        path: "(run)",
        reason: `the run produced no reviewable diff${executed.diffOmitted ? ` (${executed.diffOmitted})` : ""}`,
      });
      return outcome;
    }
    editor.report("review", `${files.length} file(s), ${executed.diff.length} chars`);
    for (const file of files) {
      let before: string | null = null;
      if (file.path) {
        before = await editor.readFile(file.path).catch(() => null);
      }
      try {
        const parsed = parseFilePatch(file.patch);
        await editor.showDiff(file.path, before, applyParsedPatch(before, parsed), "proposed");
      } catch (error) {
        if (error instanceof PatchError || error instanceof ContextError) {
          // The local file has drifted from the run's base (or the patch is not
          // text): show the patch itself rather than a fabricated comparison.
          await editor.showPatch(file.path, file.patch);
          continue;
        }
        throw error;
      }
    }
    const confirmed = await editor.confirmResult({
      files: files.map((f) => f.path),
      chars: executed.diff.length,
      review,
    });
    if (!confirmed) {
      outcome.refused.push({
        path: "(run)",
        reason: "the result was not confirmed, so the run was left awaiting its done-signal",
      });
      return outcome;
    }
    await transport.confirmDone(runId, executed.diff);
    outcome.state = "done";
    editor.report("done", "the diff is recorded; per-file review begins");
  }
  outcome.confirmed = true;

  const [diffItems, changes] = await Promise.all([
    transport.diff(runId),
    transport.changes(runId),
  ]);
  const byPath = new Map(diffItems.map((item) => [item.path, item]));

  for (const change of changes) {
    const item = byPath.get(change.path);
    if (!item) {
      outcome.refused.push({ path: change.path, reason: "the run's diff has no entry for this file" });
      continue;
    }
    if (change.reviewState === "rejected") {
      outcome.refused.push({ path: change.path, reason: "the change was already rejected" });
      continue;
    }
    if (change.reviewState === "approved" && change.diffSha !== item.diffSha) {
      outcome.refused.push({
        path: change.path,
        reason: "the approved digest is not the patch on offer; re-run to review it",
      });
      continue;
    }

    // Snapshot the file, compute the reviewed bytes, and show them *before*
    // asking — the decision must be made against what is on screen.
    let before: string | null;
    let plan2: ApplyPlan;
    try {
      before = await editor.readFile(change.path);
      plan2 = planApply(item, before);
      await editor.showDiff(change.path, before, plan2.content, "reviewed");
    } catch (error) {
      if (error instanceof PatchError || error instanceof ContextError) {
        outcome.refused.push({ path: change.path, reason: error.message });
        continue;
      }
      throw error;
    }

    let verdict: "approve" | "reject";
    if (change.reviewState === "approved") {
      // A previous review already named these bytes; don't re-ask, but do
      // verify they are still the bytes on disk.
      verdict = "approve";
    } else {
      verdict = await editor.decide(change, {
        index: changes.indexOf(change) + 1,
        total: changes.length,
      });
      const decisionWord = verdict === "approve" ? "approved" : "rejected";
      await transport.reviewChange(
        runId,
        change.id,
        decisionWord,
        item.diffSha,
        "reviewed in the editor",
      );
      outcome.reviewed.push({ path: change.path, decision: decisionWord, diffSha: item.diffSha });
    }
    if (verdict === "reject") {
      outcome.refused.push({ path: change.path, reason: "rejected during review" });
      continue;
    }

    if (options.execution.readOutput) {
      const runCopy = await options.execution.readOutput(change.path).catch(() => null);
      if (runCopy !== null && plan2.content !== null && runCopy !== plan2.content) {
        outcome.refused.push({
          path: change.path,
          reason: "the editor's applier and the confined run disagree about these bytes",
        });
        continue;
      }
    }

    // Stale check: the file must still be what the diff was computed against.
    const now = await editor.readFile(change.path);
    if (now !== before) {
      outcome.refused.push({
        path: change.path,
        reason: "the file changed after the diff was shown; re-run to review the new state",
      });
      continue;
    }
    if (plan2.content === null) {
      await editor.deleteFile(change.path);
      outcome.deleted.push(change.path);
    } else {
      await editor.writeFile(change.path, plan2.content);
      outcome.applied.push(change.path);
    }
  }

  return outcome;
}
