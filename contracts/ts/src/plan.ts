// Mirrors contracts/src/harness_contracts/plan.py.

export interface AgentPlan {
  goal: string;
  filesLikelyToChange: string[];
  steps: string[];
  tests: string[];
  risks: string[];
  estimatedCostUsd: number;
  forbiddenPaths: string[];
  expectedResult: string;
}
