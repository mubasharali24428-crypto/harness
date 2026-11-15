/**
 * UI-week leftovers on the real stack: settings section nav, palette reach,
 * the phone triage page and the checkpoints page inside the shell.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { loginViaUi } from "./support";

const TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];

test("settings nav, palette, triage and checkpoints", async ({ page }) => {
  const t0 = Date.now();
  await loginViaUi(page, `u-leftovers-${Date.now().toString(36)}@harness.dev`);
  // First visit: the five-key tour is open, and closes for good.
  await expect(page.locator(".key-tour dt")).toHaveCount(5);
  await page.getByTestId("key-tour-done").click();
  await page.goto("/demo");
  await page.getByTestId("demo-seed-button").click();
  await page.waitForURL(/\/projects\/[^/]+\/runs\/[^/?#]+/, { timeout: 60_000 });
  await expect(page.getByText("Pull request opened").first()).toBeVisible();
  // U5: a new person reaches a finished demo run in under 60 s (sign-in included).
  expect(Date.now() - t0).toBeLessThan(60_000);
  await expect(page.getByTestId("key-tour-done")).toBeHidden();
  const runPath = new URL(page.url()).pathname;

  await page.goto("/settings/team");
  await expect(page.locator('.settings-nav a[aria-current="page"]')).toHaveText("Team and invites");
  await page.locator(".settings-nav a", { hasText: "Billing and plan" }).click();
  await expect(page).toHaveURL(/\/settings\/billing/);

  await page.keyboard.press("ControlOrMeta+k");
  await page.getByPlaceholder("Where to?").fill("phone triage");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/triage/);
  await expect(page.getByText(/Nothing is waiting on you|Approve/).first()).toBeVisible({ timeout: 20_000 });
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  expect((await new AxeBuilder({ page }).withTags(TAGS).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.goto(`${runPath}/checkpoints`);
  await expect(page.getByText("← Back to the run")).toBeVisible();
  await expect(page.locator(".ws-row").first()).toBeVisible({ timeout: 20_000 });
});
