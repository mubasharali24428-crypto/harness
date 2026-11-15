/**
 * U5 phone triage on the real stack: a proposed plan shows up as pending, and
 * Approve / Deny act on it. Proposing a plan does not start a run.
 */
import { expect, test, type Page } from "@playwright/test";
import { loginViaUi } from "./support";

async function propose(page: Page, goal: string) {
  await page.goto("/");
  const composer = page.locator("#ws-composer-input");
  await composer.fill(goal);
  await composer.press("Enter");
  await page.waitForURL(/\/projects\/[^/]+\/runs\/run-/, { timeout: 60_000 });
}

test("phone triage approves one pending plan and denies another", async ({ page }) => {
  await loginViaUi(page, `u-triage-${Date.now().toString(36)}@harness.dev`);
  // A fresh team has no project; the demo seed gives it one.
  await page.goto("/demo");
  await page.getByTestId("demo-seed-button").click();
  await page.waitForURL(/\/projects\/[^/]+\/runs\/[^/?#]+/, { timeout: 60_000 });
  await propose(page, "Triage check one: add a docstring to word_count");
  await propose(page, "Triage check two: rename a local variable");

  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/triage");
  const approve = page.getByRole("button", { name: "Approve" });
  const deny = page.getByRole("button", { name: "Deny" });
  await expect(approve).toHaveCount(2, { timeout: 30_000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);

  await approve.first().click();
  await expect(page.getByText("Plan approved.")).toBeVisible();
  await expect(deny).toHaveCount(1, { timeout: 30_000 });
  await deny.first().click();
  await expect(page.getByText("Plan denied.")).toBeVisible();
  // A denied plan's run stays listed (the server still calls it awaiting a plan) with its buttons off.
  await expect(deny).toBeDisabled();
});
