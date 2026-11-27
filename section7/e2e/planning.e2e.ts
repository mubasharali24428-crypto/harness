/**
 * Sprint 2 / Task 12 — Phase 7 planning page in a real browser, API mocked (no stack needed):
 *   E2E_SKIP_STACK_CHECK=1 E2E_BASE_URL=http://localhost:3020 npx playwright test e2e/planning.e2e.ts
 */
import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const brief = { goals: ["g"], constraints: [], acceptanceCriteria: [], tasks: [{ key: "pen", title: "External pen test" }] };
const v1 = { version: 1, parentVersion: null, state: "proposed", brief, createdBy: "boss", note: null,
  assignments: [{ task: "pen", kind: "human", dependsOn: [], assignee: null, uncertainty: "unknown",
    rationale: "no authorized team member named; availability unknown", missingExpertise: ["pentester"], costEstimate: "unknown" }] };

test("admin sees an honest proposal and approves the version they reviewed", async ({ page }) => {
  const reviews: { url: string; body: unknown }[] = [];
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(e.message)); // e.g. a hydration mismatch
  await page.addInitScript(() => localStorage.setItem("harness.session", JSON.stringify({
    token: "t", principal: { userId: "boss", teamId: "team-a", role: "admin" } })));
  await page.route("**/api/v1/**", async (route) => {
    const url = route.request().url();
    if (url.endsWith("/projects/p1/assignment-plans")) {
      return route.fulfill({ json: { versions: reviews.length ? [v1, { ...v1, version: 2, parentVersion: 1, state: "approved" }] : [v1] } });
    }
    if (url.endsWith("/assignment-plans/1/review")) {
      reviews.push({ url, body: route.request().postDataJSON() });
      return route.fulfill({ status: 201, json: { ...v1, version: 2, state: "approved" } });
    }
    return route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: "not stubbed" } } });
  });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/projects/p1/planning");
  await expect(page.getByText("Unassigned: availability unknown")).toBeVisible({ timeout: 60_000 });
  const axe = await new AxeBuilder({ page }).include('[data-testid="version-1"]').withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(axe.violations.filter((x) => x.impact === "serious" || x.impact === "critical").map((x) => x.id)).toEqual([]);
  await page.getByRole("button", { name: "Approve plan" }).click();
  await expect(page.getByTestId("version-2")).toBeVisible();
  await expect(page.getByText("(older version, read-only)")).toBeVisible();
  expect(reviews).toEqual([{ url: expect.stringContaining("/1/review"), body: { decision: "approve" } }]);
  expect(pageErrors).toEqual([]);
  await page.screenshot({ path: "../docs/briefs/evidence/sprint2/task-12-planning-admin.png", fullPage: true });
});
