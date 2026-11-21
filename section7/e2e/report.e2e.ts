/**
 * Read-only run report on the real stack: a team member can read it, and a
 * person from another team gets "Run not found" (cross-team negative test).
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { loginViaUi } from "./support";

test("a member reads the report; another team cannot", async ({ page, browser }) => {
  await loginViaUi(page, `u-report-a-${Date.now().toString(36)}@harness.dev`);
  await page.goto("/demo");
  await page.getByTestId("demo-seed-button").click();
  await page.waitForURL(/\/projects\/[^/]+\/runs\/[^/?#]+/, { timeout: 60_000 });
  const reportPath = `${new URL(page.url()).pathname}/report`;

  await page.goto(reportPath);
  const report = page.getByTestId("run-report");
  await expect(report.getByRole("heading", { name: "What changed" })).toBeVisible({ timeout: 30_000 });
  await expect(report.getByTestId("evidence-cost")).toBeVisible();
  // Read-only: no way to approve, steer or stop from here.
  await expect(report.getByRole("button", { name: /approve|request changes|send|stop|start/i })).toHaveCount(0);
  await expect(page.locator("#ws-composer-input")).toHaveCount(0);
  expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);

  const other = await (await browser.newContext()).newPage();
  await loginViaUi(other, `u-report-b-${Date.now().toString(36)}@harness.dev`);
  await other.goto(reportPath);
  await expect(other.getByText("Run not found")).toBeVisible({ timeout: 30_000 });
  await expect(other.getByRole("heading", { name: "What changed" })).toHaveCount(0);
  await other.context().close();
});
