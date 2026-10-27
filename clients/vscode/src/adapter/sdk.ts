/**
 * The typed SDK as the extension's transport (W7 slice 3 adapter).
 *
 * Every call here is an ordinary product API — the same one the web and the
 * CLI use. There is no privileged endpoint and no local executor: if a
 * capability is not in the public surface, the editor does not get it.
 */

import type { Harness } from "harness-ts-sdk";

import type { ChangeView, HarnessTransport, PlanOffer } from "../core/run.js";
import type { RunDiffItem } from "../core/diff.js";

export interface PlanShape {
  goal?: string;
  expectedResult?: string;
  steps?: string[];
  filesLikelyToChange?: string[];
  risks?: string[];
  tests?: string[];
  estimatedCostUsd?: number;
}

/** Deterministic plan text — what the editor shows before approval. */
export function describePlan(plan: PlanShape): string {
  const lines: string[] = [`Goal: ${plan.goal ?? "(the request)"}`];
  if (plan.expectedResult) lines.push(`Expected: ${plan.expectedResult}`);
  if (plan.steps?.length) {
    lines.push("Steps:");
    plan.steps.forEach((step, index) => lines.push(`  ${index + 1}. ${step}`));
  }
  if (plan.filesLikelyToChange?.length) {
    lines.push(`Likely files: ${plan.filesLikelyToChange.join(", ")}`);
  }
  if (plan.risks?.length) lines.push(`Risks: ${plan.risks.join("; ")}`);
  if (plan.tests?.length) lines.push(`Tests: ${plan.tests.join("; ")}`);
  if (typeof plan.estimatedCostUsd === "number") {
    lines.push(`Estimated cost: $${plan.estimatedCostUsd}`);
  }
  return lines.join("\n");
}

interface DiffPage {
  files?: RunDiffItem[];
}

interface ChangesPage {
  items?: ChangeView[];
}

export function sdkTransport(harness: Harness, projectId: string | null = null): HarnessTransport {
  return {
    async proposePlan(goal: string): Promise<PlanOffer> {
      const response = await harness.orchestrator.propose_plan({ goal, projectId });
      return {
        planId: response.planId,
        text: describePlan((response.plan ?? {}) as PlanShape),
      };
    },
    async approvePlan(planId: string): Promise<{ runId: string }> {
      const response = await harness.orchestrator.approve_plan(planId);
      return { runId: response.runId };
    },
    async rejectPlan(planId: string): Promise<void> {
      await harness.orchestrator.reject_plan(planId);
    },
    async startRun(runId: string): Promise<void> {
      await harness.orchestrator.start_run(runId);
    },
    async status(runId: string): Promise<{ state: string }> {
      const response = await harness.orchestrator.get_run_status(runId);
      return { state: response.state };
    },
    async pendingDiff(runId: string): Promise<{ diff: string; omitted?: string | null }> {
      const engine = await harness.orchestrator.get_engine_result(runId);
      const diff = typeof engine.diff === "string" ? engine.diff : "";
      const omitted = typeof engine.diffOmitted === "string" ? engine.diffOmitted : null;
      return { diff, omitted };
    },
    async confirmDone(runId: string, diffText: string): Promise<void> {
      await harness.orchestrator.signal_done(runId, { diffText });
    },
    async diff(runId: string): Promise<RunDiffItem[]> {
      const page = (await harness.orchestrator.run_diff(runId)) as DiffPage;
      return page.files ?? [];
    },
    async changes(runId: string): Promise<ChangeView[]> {
      const page = (await harness.orchestrator.list_changes(runId)) as ChangesPage;
      return (page.items ?? []).map((item) => ({
        id: item.id,
        path: item.path,
        changeKind: item.changeKind,
        diffSha: item.diffSha,
        reviewState: item.reviewState,
      }));
    },
    async reviewChange(runId, changeId, decision, diffSha, note): Promise<void> {
      await harness.orchestrator.review_change(runId, changeId, { decision, diffSha, note });
    },
  };
}
