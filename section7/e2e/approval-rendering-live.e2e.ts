/**
 * Sprint 4 / Task 24 — LIVE approval rendering + similar-cases round-trip.
 *
 * No page.route: every /api/v1 call below goes to the real compose services
 * (identity :8001, orchestrator :8004) with the session token the app stored.
 * Run with the stack from Task 21 up:
 *   E2E_BASE_URL=http://localhost:3010 E2E_API_URL=http://localhost:8001 \
 *   E2E_ORCHESTRATOR_URL=http://localhost:8004 \
 *   npx playwright test e2e/approval-rendering-live.e2e.ts --retries=0
 */
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { journeyEmail, loginViaUi } from "./support";

const identity = process.env.E2E_IDENTITY_URL ?? "http://localhost:8001";
const orch = process.env.E2E_ORCHESTRATOR_URL ?? "http://localhost:8004";
const SHOTS = "../docs/briefs/evidence/sprint4";

async function call(page: Page, base: string, path: string, method = "GET", data?: object) {
  const token = await page.evaluate(() =>
    (JSON.parse(localStorage.getItem("harness.session") ?? "null") as { token?: string } | null)?.token ?? "");
  expect(token, "the app must have stored a real session token").not.toBe("");
  const res = await page.request.fetch(`${base}${path}`, {
    method,
    data,
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
  });
  const text = await res.text();
  return { status: res.status(), body: text ? JSON.parse(text) : null };
}

test("live: settings rendering change affects the next real approval fetch", async ({ page }) => {
  const email = journeyEmail();
  await loginViaUi(page, email); // real dev-bypass login (ownership off on this stack)

  // 1. real section1 rendering API, default then self-service change
  const before = await call(page, identity, "/api/v1/me/rendering");
  expect(before.status).toBe(200);
  expect(before.body.rendering).toBe("technical");
  const put = await call(page, identity, "/api/v1/me/rendering", "PUT", { rendering: "plain" });
  expect(put.status).toBe(200);
  expect((await call(page, identity, "/api/v1/me/rendering")).body.rendering).toBe("plain");

  // 2. seed a real collab approval through the documented orchestrator APIs
  const plan = await call(page, orch, "/api/v1/orchestrator/plans", "POST", {
    goal: "Live approval rendering check",
  });
  expect(plan.status, JSON.stringify(plan.body)).toBe(201);
  const runId = plan.body.runId as string;
  const projectId = (plan.body.projectId as string | null) ?? "p";
  expect((await call(page, orch, `/api/v1/orchestrator/runs/${runId}/join`, "POST")).status).toBe(200);
  expect((await call(page, orch, `/api/v1/orchestrator/runs/${runId}/leader`, "POST")).status).toBe(200);
  const requested = await call(page, orch, `/api/v1/orchestrator/runs/${runId}/approvals`, "POST", {
    action: "modify", paths: ["deploy/.env"], summary: "rotate the API key", diff: "-OLD\n+NEW",
  });
  expect(requested.status, JSON.stringify(requested.body)).toBe(201);

  // 3. real list response carries server-computed rendering + similar cases
  const listed = await call(page, orch, `/api/v1/orchestrator/runs/${runId}/approvals`);
  expect(listed.status).toBe(200);
  const approval = listed.body.approvals.find((a: { id: string }) => a.id === requested.body.id);
  expect(approval, "the real approval must be in the real list").toBeTruthy();
  expect(approval.plain).toBeTruthy();
  expect(approval.decisionGrid).toBeTruthy();
  expect(Array.isArray(approval.similarCases)).toBe(true); // empty on first denial is honest

  // 4. the UI renders THAT response (no mock) with the preference from step 1
  await page.goto(`/projects/${encodeURIComponent(projectId)}/runs/${runId}?pane=advanced`);
  const card = page.getByTestId(`approval-${approval.id}`);
  await expect(card).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId(`plain-${approval.id}`)).toHaveText(approval.plain);
  await card.screenshot({ path: `${SHOTS}/live-plain-approval.png` });

  // 5. change the preference through the shipped settings UI, on the stack
  await page.goto("/settings/account");
  await page.getByLabel("Technical").check();
  await page.getByRole("button", { name: "Save rendering" }).click();
  await expect(page.getByText("Rendering saved.", { exact: false })).toBeVisible();
  expect((await call(page, identity, "/api/v1/me/rendering")).body.rendering).toBe("technical");

  // 6. 375 px + axe regression guard on the live decision card
  await page.goto(`/projects/${encodeURIComponent(projectId)}/runs/${runId}?pane=advanced`);
  await page.setViewportSize({ width: 375, height: 812 });
  const live = page.getByTestId(`approval-${approval.id}`);
  await expect(live).toBeVisible({ timeout: 60_000 });
  const results = await new AxeBuilder({ page })
    .include(`[data-testid="approval-${approval.id}"]`)
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  await live.screenshot({ path: `${SHOTS}/live-plain-375.png` });
});
