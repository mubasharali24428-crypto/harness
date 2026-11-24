// Sprint 3 / Task 15 — honest mock upgrade (Task 12 stack smoke was BLOCKED).
// The browser specs cannot hit a live section4, so prove the fixed mock still
// matches section4's documented request contract. If the contract drifts, this
// fails instead of the e2e suite quietly testing an impossible payload.
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { APPROVAL } from "./approvalFixture";

const openapi = JSON.parse(
  readFileSync(path.resolve(process.cwd(), "../section4/openapi.json"), "utf8"),
) as {
  components: { schemas: Record<string, {
    properties: Record<string, { type?: string; enum?: string[]; maxItems?: number; maxLength?: number }>;
    required?: string[];
  }> };
  paths: Record<string, Record<string, { responses: Record<string, {
    content: Record<string, { schema: { type?: string } }>;
  }> }>>;
};

describe("approval e2e mock ↔ openapi parity", () => {
  const schema = openapi.components.schemas.ApprovalRequest;

  it("the mock's request fields still satisfy the openapi ApprovalRequest limits", () => {
    for (const key of schema.required ?? []) expect(APPROVAL).toHaveProperty(key);
    expect(schema.properties.action.enum).toContain(APPROVAL.action);
    expect(APPROVAL.paths.length).toBeLessThanOrEqual(schema.properties.paths.maxItems!);
    expect(APPROVAL.summary.length).toBeLessThanOrEqual(schema.properties.summary.maxLength!);
    expect(APPROVAL.diff!.length).toBeLessThanOrEqual(schema.properties.diff.maxLength!);
    expect(APPROVAL.digest).toMatch(/^[0-9a-f]{64}$/);
  });

  it("the approvals list route is still documented with an object response", () => {
    const route = openapi.paths["/api/v1/orchestrator/runs/{runId}/approvals"].get;
    expect(route.responses["200"].content["application/json"].schema.type).toBe("object");
  });
});
