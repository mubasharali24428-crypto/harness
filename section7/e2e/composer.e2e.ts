/**
 * R3 journey (docs/briefs/ui-master-prompt, phase R3): on a live, non-replay
 * run — propose → edit the plan → approve → start → steer → stop — driven by
 * the keyboard only (controls are focused, then operated with keys).
 */
import { expect, test, type Page } from "@playwright/test";
import { loginViaUi } from "./support";

async function press(page: Page, testId: string) {
  await expect(page.getByTestId(testId)).toBeEnabled({ timeout: 30_000 }); // Enter on a busy button does nothing
  await page.getByTestId(testId).focus();
  await page.keyboard.press("Enter");
}

test("propose, edit plan, approve, start, steer and stop with the keyboard", async ({ page }) => {
  await loginViaUi(page, `r3-composer-${Date.now().toString(36)}@harness.dev`);
  // A fresh team has no project; the demo seed gives it one, then we start a real run.
  await page.goto("/demo");
  await page.getByTestId("demo-seed-button").click();
  await page.waitForURL(/\/projects\/[^/]+\/runs\/[^/?#]+/, { timeout: 60_000 });

  await page.goto("/");
  const composer = page.locator("#ws-composer-input");
  await composer.focus();
  await page.keyboard.type("Add a function word_count(text) to textstats and a test for it");
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/projects\/[^/]+\/runs\/run-/, { timeout: 60_000 });
  await expect(page.getByTestId("demo-replay-banner")).toHaveCount(0); // not a replay
  await expect(composer).toBeDisabled();
  await expect(page.locator("#ws-composer-help")).toContainText("Approve or edit the plan");

  await press(page, "plan-edit");
  await page.getByLabel("Steps, one per line").focus();
  await page.keyboard.press("ControlOrMeta+End");
  await page.keyboard.type("\nRun the full test suite before finishing");
  await press(page, "plan-save");
  await expect(page.locator(".ws-plan-steps")).toContainText("Run the full test suite before finishing");
  await expect(page.getByTestId("plan-pin")).not.toContainText("{");

  await press(page, "plan-approve");
  await press(page, "plan-start");
  await expect(composer).toBeEnabled({ timeout: 60_000 });

  await composer.focus();
  await page.keyboard.type("Keep the public API unchanged");
  await page.keyboard.press("Enter");
  await expect(page.locator(".ws-sent li")).toHaveCount(1);
  await expect(page.locator(".ws-sent-status")).not.toBeEmpty();

  await composer.focus();
  await page.keyboard.type("/stop");
  await page.keyboard.press("Enter");
  await expect(page.locator(".ws-composer-note")).toContainText(/stop/i);
  await expect(page.getByTestId("next-step")).toBeVisible({ timeout: 60_000 });
});
